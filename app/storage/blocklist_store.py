"""公司与岗位屏蔽名单的 SQLite 持久化层。

被关键词扣星或 AI 筛选过滤掉的公司岗位会自动写入屏蔽名单；后续投递在评分前即可
命中并跳过，省去重复解析与大模型调用。屏蔽记录永久有效，只能由管理端手动移除或
手动新增。判重键复用 :func:`app.storage.delivery_store.delivery_key`，因此屏蔽粒度与
投递去重保持一致（公司 + 岗位）。

自动拉黑是高频写入（每个被过滤的岗位都会写一次），因此本模块用“单个常驻连接 +
一把进程内锁”串行化所有读写，而不是像投递库那样每次操作开新连接。后者在数十个
浏览器线程并发过滤时会因连接开关开销和多连接争抢 WAL 写锁把写操作放大数百倍，
拖慢评分主流程；常驻连接把每次写压回毫秒级，且写锁只在插入瞬间短暂持有。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
import threading

from app.storage.delivery_store import delivery_key


# 自动屏蔽的两种来源；手动新增使用 ``manual``。
BLOCK_REASONS = {'below_threshold', 'ai_rejected', 'manual'}


class BlocklistStore:
    """基于 SQLite 的公司岗位屏蔽名单。

    ``db_path`` 与投递协调库共用同一数据库文件。所有操作通过一个常驻连接执行，并由
    ``_lock`` 串行化，避免高频自动拉黑写入与投递主流程争抢数据库锁；``company_key``
    为主键，天然保证同一公司岗位只有一条屏蔽记录。
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(
            self.db_path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute('PRAGMA busy_timeout = 30000')
        self._connection.execute('PRAGMA journal_mode = WAL')
        # 屏蔽名单是可由过滤流程随时重建的优化性数据：丢失少量记录只会让个别岗位少
        # 跳过一次，不影响投递正确性。因此用 NORMAL 而非 FULL，减少每次写入的 fsync。
        self._connection.execute('PRAGMA synchronous = NORMAL')
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS company_blocklist (
                    company_key TEXT PRIMARY KEY,
                    company TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL,
                    score INTEGER,
                    ai_reason TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_company_blocklist_company
                    ON company_blocklist(company);
                """
            )

    def close(self) -> None:
        """关闭常驻连接；进程退出或切换数据库文件时调用。"""
        with self._lock:
            self._connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec='seconds')

    @staticmethod
    def _normalize_reason(reason: str) -> str:
        reason = (reason or '').strip()
        return reason if reason in BLOCK_REASONS else 'manual'

    def is_blocked(self, company: str, title: str = '') -> dict:
        """查询指定公司与岗位是否已屏蔽；只读，不占用额度。"""
        company_key = delivery_key(company, title)
        if not company_key:
            return {'blocked': False, 'reason': 'missing_company'}
        with self._lock:
            row = self._connection.execute(
                'SELECT * FROM company_blocklist WHERE company_key = ?',
                (company_key,),
            ).fetchone()
        return {'blocked': bool(row), 'entry': dict(row) if row else None}

    def block(
        self,
        *,
        company: str,
        title: str,
        reason: str,
        score: int | None = None,
        ai_reason: str = '',
        note: str = '',
    ) -> dict:
        """幂等写入一条屏蔽记录。

        同一公司岗位已存在时刷新原因、分数与 AI 理由但保留最初的 ``created_at`` 与
        ``note``（自动拉黑不覆盖手动备注）；公司名无有效字符时以业务结果返回，不抛异常。
        """
        company = (company or '').strip()
        title = (title or '').strip()
        company_key = delivery_key(company, title)
        if not company_key:
            return {'success': False, 'reason': 'missing_company'}
        reason = self._normalize_reason(reason)
        score_value = int(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None
        now = self._now()
        with self._lock:
            exists = self._connection.execute(
                'SELECT 1 FROM company_blocklist WHERE company_key = ?',
                (company_key,),
            ).fetchone()
            self._connection.execute(
                """
                INSERT INTO company_blocklist(
                    company_key, company, title, reason, score, ai_reason, note,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_key) DO UPDATE SET
                    company = excluded.company,
                    title = excluded.title,
                    reason = excluded.reason,
                    score = excluded.score,
                    ai_reason = excluded.ai_reason,
                    updated_at = excluded.updated_at
                """,
                (
                    company_key,
                    company,
                    title,
                    reason,
                    score_value,
                    (ai_reason or '')[:1000],
                    (note or '')[:1000],
                    now,
                    now,
                ),
            )
        return {
            'success': True,
            'companyKey': company_key,
            'created': not exists,
            'reason': reason,
        }

    def list_blocked(self, keyword: str = '', reason: str = '') -> list[dict]:
        """按公司/岗位关键词与原因筛选屏蔽记录，最新创建的在前。"""
        clauses = []
        params: list[object] = []
        keyword = (keyword or '').strip()
        if keyword:
            clauses.append('(company LIKE ? OR title LIKE ?)')
            like = f'%{keyword}%'
            params.extend([like, like])
        reason = (reason or '').strip()
        if reason in BLOCK_REASONS:
            clauses.append('reason = ?')
            params.append(reason)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ''
        with self._lock:
            rows = self._connection.execute(
                f'SELECT * FROM company_blocklist {where} ORDER BY created_at DESC, company_key',
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def unblock(self, company_keys=None, jobs=None) -> dict:
        """按屏蔽键或“公司、岗位”集合移除屏蔽记录，返回删除数量与原记录。"""
        keys = {str(key).strip() for key in (company_keys or []) if str(key).strip()}
        keys |= {
            delivery_key(company, title)
            for company, title in (jobs or [])
            if delivery_key(company, title)
        }
        keys = {key for key in keys if key}
        if not keys:
            return {'deleted': 0, 'records': []}
        ordered = sorted(keys)
        placeholders = ','.join('?' for _ in ordered)
        with self._lock:
            rows = self._connection.execute(
                f'SELECT * FROM company_blocklist WHERE company_key IN ({placeholders})',
                ordered,
            ).fetchall()
            self._connection.execute(
                f'DELETE FROM company_blocklist WHERE company_key IN ({placeholders})',
                ordered,
            )
        return {'deleted': len(rows), 'records': [dict(row) for row in rows]}

    def count(self) -> int:
        """返回屏蔽记录总数；只读。"""
        with self._lock:
            row = self._connection.execute(
                'SELECT COUNT(*) AS total FROM company_blocklist'
            ).fetchone()
        return int(row['total']) if row else 0
