"""投递记录、账号每日额度与跨客户端去重的 SQLite 持久化层。

本模块把“检查重复岗位、占用每日额度、更新投递状态”放在数据库事务中完成。
每次操作使用独立连接，SQLite WAL 模式负责跨线程、跨进程协调；调用方不应绕过
本模块直接修改相关数据表。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import re
import secrets
import sqlite3
import unicodedata

from app.storage.io import read_jsonl


FINAL_STATUSES = {'sent', 'failed_unknown'}
FINAL_ACTION_STATUSES = {
    'greet_sent': 'sent',
    'greet_message_sent': 'sent',
    'chat_greet_sent': 'sent',
    'greet_failed': 'failed_unknown',
    'chat_greet_failed': 'failed_unknown',
    'greet_queue_failed': 'failed_unknown',
}
DELIVERY_SOURCE_ACTIONS = {
    'delivery_claimed',
    'greet_queued',
    *FINAL_ACTION_STATUSES,
}
HISTORICAL_DELIVERY_ACTIONS = {
    'greet_queued',
    'greet_sent',
    'greet_message_sent',
    'chat_greet_sent',
}


def normalize_company(company: str) -> str:
    """将原始公司名规范化为可比较的键；返回空串表示公司名无有效字符。"""
    normalized = unicodedata.normalize('NFKC', company or '').casefold().strip()
    return re.sub(r'[\W_]+', '', normalized, flags=re.UNICODE)


def normalize_title(title: str) -> str:
    """将原始岗位名规范化为可比较的键，不读取或修改持久化数据。"""
    normalized = unicodedata.normalize('NFKC', title or '').casefold().strip()
    return re.sub(r'[\W_]+', '', normalized, flags=re.UNICODE)


def delivery_key(company: str, title: str) -> str:
    """组合公司和岗位规范化键，供数据库判重；公司为空时返回空串。"""
    company_key = normalize_company(company)
    if not company_key:
        return ''
    return f'{company_key}\x1f{normalize_title(title)}'


class DeliveryStore:
    """基于 SQLite 的跨线程、跨进程投递协调器。

    ``db_path`` 指向持久化数据库，``daily_limit`` 是单账号每日上限。写操作使用
    ``BEGIN IMMEDIATE`` 串行化关键检查，确保判重、额度校验和记录写入不会竞态。
    """

    def __init__(self, db_path: Path | str, daily_limit: int = 90):
        """创建存储实例并初始化表结构；必要时会创建目录及迁移旧格式记录。"""
        self.db_path = Path(db_path)
        self.daily_limit = max(1, int(daily_limit))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        """创建一次操作专用的连接，调用方负责关闭或交给 ``_connection`` 托管。"""
        connection = sqlite3.connect(
            self.db_path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA busy_timeout = 30000')
        return connection

    @contextmanager
    def _connection(self):
        """提供自动关闭的短连接上下文；不会自动开启显式事务。"""
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        """创建表、索引并迁移旧判重键；构造实例时同步执行且会写数据库。"""
        with self._connection() as connection:
            connection.execute('PRAGMA journal_mode = WAL')
            connection.execute('PRAGMA synchronous = FULL')
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS company_deliveries (
                    company_key TEXT PRIMARY KEY,
                    company TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    job_url TEXT NOT NULL DEFAULT '',
                    account_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    claim_token TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    queued_at TEXT,
                    completed_at TEXT,
                    last_error TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_company_deliveries_account
                    ON company_deliveries(account_id, status);

                CREATE TABLE IF NOT EXISTS daily_account_usage (
                    usage_date TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0 CHECK(count >= 0),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (usage_date, account_id)
                );

                CREATE TABLE IF NOT EXISTS delivery_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            rows = connection.execute(
                """
                SELECT company_key, company, title, status, claimed_at
                FROM company_deliveries
                ORDER BY
                    CASE status
                        WHEN 'sent' THEN 4
                        WHEN 'failed_unknown' THEN 3
                        WHEN 'queued' THEN 2
                        ELSE 1
                    END DESC,
                    claimed_at DESC
                """
            ).fetchall()
            winners = {}
            for row in rows:
                expected = delivery_key(row['company'], row['title'])
                if expected and expected not in winners:
                    winners[expected] = row
                else:
                    connection.execute(
                        'DELETE FROM company_deliveries WHERE company_key = ?',
                        (row['company_key'],),
                    )
            # 先改成随机临时键，再统一写入新键，避免旧版“仅公司键”迁移时触发唯一键冲突。
            pending_updates = []
            for expected, row in winners.items():
                if row['company_key'] != expected:
                    temporary = f'__goodjobs_migrate__{row["company_key"]}__{secrets.token_hex(6)}'
                    connection.execute(
                        'UPDATE company_deliveries SET company_key = ? WHERE company_key = ?',
                        (temporary, row['company_key']),
                    )
                    pending_updates.append((expected, temporary))
            for expected, temporary in pending_updates:
                connection.execute(
                    'UPDATE company_deliveries SET company_key = ? WHERE company_key = ?',
                    (expected, temporary),
                )

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec='seconds')

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime('%Y-%m-%d')

    def quota_status(self, account_id: str) -> dict:
        """查询账号当日用量与剩余额度；仅执行数据库读取，不占用额度。"""
        account_id = (account_id or '').strip()
        if not account_id:
            return {
                'date': self._today(),
                'accountId': '',
                'count': 0,
                'limit': self.daily_limit,
                'remaining': 0,
                'reached': True,
                'reason': 'missing_account_id',
            }
        with self._connection() as connection:
            row = connection.execute(
                'SELECT count FROM daily_account_usage WHERE usage_date = ? AND account_id = ?',
                (self._today(), account_id),
            ).fetchone()
        count = int(row['count']) if row else 0
        return {
            'date': self._today(),
            'accountId': account_id,
            'count': count,
            'limit': self.daily_limit,
            'remaining': max(0, self.daily_limit - count),
            'reached': count >= self.daily_limit,
        }

    def increment_usage(self, account_id: str) -> dict:
        """为旧客户端原子增加一次当日用量并返回最新额度状态。

        空账号会归入 ``legacy``。该方法会持久化计数但不会创建岗位记录；新客户端
        应调用 :meth:`claim`，以便判重与占额在同一事务中完成。
        """
        account_id = (account_id or '').strip() or 'legacy'
        today = self._today()
        now = self._now()
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute(
                """
                INSERT INTO daily_account_usage(usage_date, account_id, count, updated_at)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(usage_date, account_id) DO NOTHING
                """,
                (today, account_id, now),
            )
            row = connection.execute(
                'SELECT count FROM daily_account_usage WHERE usage_date = ? AND account_id = ?',
                (today, account_id),
            ).fetchone()
            count = int(row['count'])
            if count < self.daily_limit:
                count += 1
                connection.execute(
                    """
                    UPDATE daily_account_usage SET count = ?, updated_at = ?
                    WHERE usage_date = ? AND account_id = ?
                    """,
                    (count, now, today, account_id),
                )
            connection.commit()
            return {
                'date': today,
                'accountId': account_id,
                'count': count,
                'limit': self.daily_limit,
                'remaining': max(0, self.daily_limit - count),
                'reached': count >= self.daily_limit,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim(
        self,
        *,
        company: str,
        title: str,
        account_id: str,
        worker_id: str,
        job_url: str = '',
        daily_limit: int | None = None,
    ) -> dict:
        """原子申请一个岗位投递占位并占用账号当日额度。

        输入公司、岗位、账号、工作器标识及可选岗位链接。返回 ``accepted``、原因和
        成功时的 ``claimToken``；重复岗位、字段缺失或额度耗尽均以业务结果返回。
        ``BEGIN IMMEDIATE`` 保证并发客户端无法同时通过判重或额度检查。
        """
        company = (company or '').strip()
        title = (title or '').strip()
        account_id = (account_id or '').strip()
        worker_id = (worker_id or '').strip()
        company_key = delivery_key(company, title)
        if not company_key:
            return {'accepted': False, 'reason': 'missing_company'}
        if not normalize_title(title):
            return {'accepted': False, 'reason': 'missing_title'}
        if not account_id:
            return {'accepted': False, 'reason': 'missing_account_id'}
        if not worker_id:
            return {'accepted': False, 'reason': 'missing_worker_id'}

        today = self._today()
        now = self._now()
        effective_limit = self.daily_limit if daily_limit is None else max(1, int(daily_limit))
        claim_token = secrets.token_urlsafe(24)
        connection = self._connect()
        try:
            # 在读取重复记录和额度前取得写锁，使后续检查与两项写入构成一个原子操作。
            connection.execute('BEGIN IMMEDIATE')
            existing = connection.execute(
                'SELECT company, title, account_id, status, claimed_at FROM company_deliveries WHERE company_key = ?',
                (company_key,),
            ).fetchone()
            if existing:
                connection.rollback()
                return {
                    'accepted': False,
                    'reason': 'duplicate_job',
                    'existing': dict(existing),
                }

            connection.execute(
                """
                INSERT INTO daily_account_usage(usage_date, account_id, count, updated_at)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(usage_date, account_id) DO NOTHING
                """,
                (today, account_id, now),
            )
            usage = connection.execute(
                'SELECT count FROM daily_account_usage WHERE usage_date = ? AND account_id = ?',
                (today, account_id),
            ).fetchone()
            count = int(usage['count'])
            if count >= effective_limit:
                connection.rollback()
                return {
                    'accepted': False,
                    'reason': 'daily_limit',
                    'count': count,
                    'limit': effective_limit,
                    'remaining': 0,
                }

            connection.execute(
                """
                INSERT INTO company_deliveries(
                    company_key, company, title, job_url, account_id, worker_id,
                    claim_token, status, claimed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', ?)
                """,
                (
                    company_key,
                    company,
                    title,
                    job_url or '',
                    account_id,
                    worker_id,
                    claim_token,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE daily_account_usage
                SET count = count + 1, updated_at = ?
                WHERE usage_date = ? AND account_id = ?
                """,
                (now, today, account_id),
            )
            connection.commit()
            return {
                'accepted': True,
                'reason': 'claimed',
                'claimToken': claim_token,
                'companyKey': company_key,
                'count': count + 1,
                'limit': effective_limit,
                'remaining': max(0, effective_limit - count - 1),
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark(self, claim_token: str, status: str, error: str = '') -> dict:
        """按占位令牌原子推进投递状态，并返回是否成功及是否为幂等更新。

        ``status`` 仅允许 ``queued``、``sent``、``failed_unknown``。终态不会被普通
        重试回退；错误文本最多持久化 1000 个字符。
        """
        if status not in {'queued', 'sent', 'failed_unknown'}:
            raise ValueError(f'unsupported delivery status: {status}')
        claim_token = (claim_token or '').strip()
        now = self._now()
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT status FROM company_deliveries WHERE claim_token = ?',
                (claim_token,),
            ).fetchone()
            if not row:
                connection.rollback()
                return {'success': False, 'reason': 'claim_not_found'}

            current = row['status']
            if current == 'sent':
                connection.rollback()
                return {'success': True, 'status': current, 'idempotent': True}
            if current == 'failed_unknown' and status != 'sent':
                connection.rollback()
                return {'success': True, 'status': current, 'idempotent': True}

            queued_at = now if status == 'queued' else None
            completed_at = now if status in FINAL_STATUSES else None
            connection.execute(
                """
                UPDATE company_deliveries
                SET status = ?,
                    queued_at = COALESCE(queued_at, ?),
                    completed_at = COALESCE(completed_at, ?),
                    last_error = ?
                WHERE claim_token = ?
                """,
                (status, queued_at, completed_at, (error or '')[:1000], claim_token),
            )
            connection.commit()
            return {'success': True, 'status': status, 'idempotent': current == status}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim_status(self, claim_token: str) -> dict:
        """按占位令牌读取投递状态；返回 ``exists`` 与可选的 ``delivery`` 记录。"""
        claim_token = (claim_token or '').strip()
        if not claim_token:
            return {'exists': False, 'reason': 'missing_claim_token'}
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT company, title, account_id, worker_id, status, claimed_at,
                       queued_at, completed_at
                FROM company_deliveries
                WHERE claim_token = ?
                """,
                (claim_token,),
            ).fetchone()
        return {'exists': bool(row), 'delivery': dict(row) if row else None}

    def delete_history(self, claim_tokens=None, jobs=None) -> dict:
        """按令牌或“公司、岗位”集合原子删除已结束的投递历史。

        返回删除数量和原记录；发现 ``reserved``/``queued`` 记录时整批回滚并抛出
        ``ValueError``。删除不会返还每日额度，避免通过删记录绕过上限。
        """
        tokens = {str(token).strip() for token in (claim_tokens or []) if str(token).strip()}
        job_keys = {
            delivery_key(company, title)
            for company, title in (jobs or [])
            if delivery_key(company, title)
        }
        clauses = []
        params = []
        if tokens:
            clauses.append(f"claim_token IN ({','.join('?' for _ in tokens)})")
            params.extend(sorted(tokens))
        if job_keys:
            clauses.append(f"company_key IN ({','.join('?' for _ in job_keys)})")
            params.extend(sorted(job_keys))
        if not clauses:
            return {'deleted': 0, 'records': []}

        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute(
                f"""
                SELECT company_key, company, title, account_id, claim_token, status, claimed_at
                FROM company_deliveries
                WHERE {' OR '.join(clauses)}
                """,
                params,
            ).fetchall()
            active = [dict(row) for row in rows if row['status'] in {'reserved', 'queued'}]
            if active:
                connection.rollback()
                names = '、'.join(f"{row['company']} / {row['title']}" for row in active[:5])
                raise ValueError(f'进行中的投递记录不能删除: {names}')
            keys = [row['company_key'] for row in rows]
            if keys:
                connection.execute(
                    f"DELETE FROM company_deliveries WHERE company_key IN ({','.join('?' for _ in keys)})",
                    keys,
                )
            connection.commit()
            return {'deleted': len(rows), 'records': [dict(row) for row in rows]}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def release(self, claim_token: str, reason: str = '') -> dict:
        """原子释放尚未发起沟通的占位，并返还对应账号的当日名额。

        仅 ``reserved`` 状态可释放；不存在或已经开始的记录以业务结果返回，不抛异常。
        """
        claim_token = (claim_token or '').strip()
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT account_id, status, claimed_at FROM company_deliveries WHERE claim_token = ?',
                (claim_token,),
            ).fetchone()
            if not row:
                connection.rollback()
                return {'success': False, 'reason': 'claim_not_found'}
            if row['status'] != 'reserved':
                connection.rollback()
                return {'success': False, 'reason': 'already_started', 'status': row['status']}

            # 归还名额必须落在“领取当天”的计数上：claim 在领取当日累加，若占位跨过午夜再释放，
            # 按今天扣减会错扣当天配额并让领取日计数永久虚高。claimed_at 的日期前缀与 usage_date 同格式。
            claim_date = (row['claimed_at'] or '')[:10] or self._today()
            connection.execute('DELETE FROM company_deliveries WHERE claim_token = ?', (claim_token,))
            connection.execute(
                """
                UPDATE daily_account_usage
                SET count = MAX(0, count - 1), updated_at = ?
                WHERE usage_date = ? AND account_id = ?
                """,
                (self._now(), claim_date, row['account_id']),
            )
            connection.commit()
            return {'success': True, 'released': True, 'reason': (reason or '')[:1000]}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def company_status(self, company: str, title: str = '') -> dict:
        """查询指定公司与岗位是否已有投递记录；该操作只读且不占用额度。"""
        company_key = delivery_key(company, title)
        if not company_key:
            return {'exists': False, 'reason': 'missing_company'}
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT company, title, account_id, worker_id, status, claimed_at,
                       queued_at, completed_at
                FROM company_deliveries
                WHERE company_key = ?
                ORDER BY claimed_at DESC LIMIT 1
                """,
                (company_key,),
            ).fetchone()
        return {'exists': bool(row), 'delivery': dict(row) if row else None}

    def status_indexes(self) -> tuple[dict[str, dict], dict[tuple[str, str], dict]]:
        """Return all delivery states indexed by claim token and original job identity."""
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT company, title, account_id, claim_token, status,
                           claimed_at, queued_at, completed_at
                    FROM company_deliveries
                    """
                ).fetchall()
        except (sqlite3.Error, OSError):
            return {}, {}

        by_token: dict[str, dict] = {}
        by_job: dict[tuple[str, str], dict] = {}
        for row in rows:
            item = dict(row)
            token = item.get('claim_token') or ''
            if token:
                by_token[token] = item
            key = ((item.get('company') or '').strip(), (item.get('title') or '').strip())
            if key[0]:
                by_job[key] = item
        return by_token, by_job

    def record_legacy_sent(self, company: str, title: str, account_id: str = 'legacy') -> dict:
        """兼容旧客户端，幂等写入已投递记录但不占用或修改每日额度。

        返回 ``success`` 和 ``duplicate``；数据库唯一键负责并发导入时的去重。
        """
        company = (company or '').strip()
        title = (title or '').strip()
        company_key = delivery_key(company, title)
        if not company_key:
            return {'success': False, 'reason': 'missing_company'}
        now = self._now()
        token = f'legacy-{secrets.token_urlsafe(18)}'
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO company_deliveries(
                    company_key, company, title, account_id, worker_id, claim_token,
                    status, claimed_at, completed_at
                ) VALUES (?, ?, ?, ?, 'legacy', ?, 'sent', ?, ?)
                """,
                (company_key, company, title, account_id, token, now, now),
            )
        return {'success': True, 'duplicate': cursor.rowcount == 0}

    def import_legacy_jsonl(self, path: Path | str) -> int:
        """从旧 JSONL 文件导入公司和岗位，跳过坏行并返回新增记录数。"""
        imported = 0
        for record in read_jsonl(path):
            company = record.get('company') or ''
            if company and not self.record_legacy_sent(company, record.get('title') or '').get('duplicate'):
                imported += 1
        return imported

    def import_action_log(self, path: Path | str) -> int:
        """从历史动作 JSONL 恢复可能已沟通的岗位，并返回新增记录数。

        仅识别投递相关动作，坏行会被忽略。导入会写数据库但不占每日额度，策略上采用
        “宁可少投、不重复投”。
        """
        imported = 0
        for record in read_jsonl(path):
            if record.get('action') not in HISTORICAL_DELIVERY_ACTIONS:
                continue
            company = record.get('company') or ''
            if company and not self.record_legacy_sent(company, record.get('title') or '').get('duplicate'):
                imported += 1
        return imported

    def _import_file_once(self, migration_key: str, path: Path | str, importer) -> int:
        """Run one legacy-file importer only after its source is actually available."""
        source = Path(path)
        if not source.exists() or not source.is_file():
            return 0
        with self._connection() as connection:
            completed = connection.execute(
                'SELECT 1 FROM delivery_metadata WHERE key = ?',
                (migration_key,),
            ).fetchone()
        if completed:
            return 0
        imported = importer(source)
        with self._connection() as connection:
            connection.execute(
                'INSERT OR IGNORE INTO delivery_metadata(key, value) VALUES (?, ?)',
                (migration_key, self._now()),
            )
        return imported

    def import_legacy_once(self, greeted_path: Path | str, action_path: Path | str) -> int:
        """Import each pre-SQLite source once, without marking missing files complete."""
        return self._import_file_once(
            'legacy_greeted_jsonl_v1',
            greeted_path,
            self.import_legacy_jsonl,
        ) + self._import_file_once(
            'legacy_action_jsonl_v1',
            action_path,
            self.import_action_log,
        )
