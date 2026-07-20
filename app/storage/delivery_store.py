"""投递记录、账号每日额度与跨客户端去重的 SQLite 持久化层。

本模块把“检查重复岗位、占用每日额度、更新投递状态”放在数据库事务中完成。
每次操作使用独立连接，SQLite WAL 模式负责跨线程、跨进程协调；调用方不应绕过
本模块直接修改相关数据表。
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import re
import secrets
import sqlite3
import time
import unicodedata

from app.storage.io import read_jsonl


FINAL_STATUSES = {'sent', 'failed_unknown'}
RESERVED_LEASE_SECONDS = 15 * 60
AI_EVALUATION_LEASE_SECONDS = 15 * 60
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
                    qualification_fingerprint TEXT NOT NULL DEFAULT '',
                    ai_fingerprint TEXT NOT NULL DEFAULT '',
                    ai_required INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    claimed_at_epoch REAL NOT NULL DEFAULT 0,
                    lease_expires_at REAL NOT NULL DEFAULT 0,
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

                CREATE TABLE IF NOT EXISTS ai_decisions (
                    ai_fingerprint TEXT PRIMARY KEY,
                    passed INTEGER NOT NULL CHECK(passed IN (0, 1)),
                    reason TEXT NOT NULL DEFAULT '',
                    decided_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ai_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    ai_fingerprint TEXT NOT NULL UNIQUE,
                    cache_fingerprint TEXT NOT NULL DEFAULT '',
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    passed INTEGER,
                    reason TEXT NOT NULL DEFAULT '',
                    reliable INTEGER NOT NULL DEFAULT 0,
                    cached INTEGER NOT NULL DEFAULT 0,
                    lease_expires_at REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column(
                connection,
                'company_deliveries',
                'qualification_fingerprint',
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                'company_deliveries',
                'ai_fingerprint',
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                'company_deliveries',
                'ai_required',
                'INTEGER NOT NULL DEFAULT 0',
            )
            self._ensure_column(
                connection,
                'company_deliveries',
                'claimed_at_epoch',
                'REAL NOT NULL DEFAULT 0',
            )
            self._ensure_column(
                connection,
                'company_deliveries',
                'lease_expires_at',
                'REAL NOT NULL DEFAULT 0',
            )
            self._ensure_column(
                connection,
                'ai_evaluations',
                'cache_fingerprint',
                "TEXT NOT NULL DEFAULT ''",
            )
            connection.execute(
                """
                UPDATE ai_evaluations
                SET cache_fingerprint = ai_fingerprint
                WHERE cache_fingerprint = ''
                """
            )
            legacy_reserved = connection.execute(
                """
                SELECT claim_token, account_id, claimed_at
                FROM company_deliveries
                WHERE status = 'reserved' AND qualification_fingerprint = ''
                """
            ).fetchall()
            for legacy_claim in legacy_reserved:
                claim_date = (legacy_claim['claimed_at'] or '')[:10] or self._today()
                connection.execute(
                    'DELETE FROM company_deliveries WHERE claim_token = ?',
                    (legacy_claim['claim_token'],),
                )
                connection.execute(
                    """
                    UPDATE daily_account_usage
                    SET count = MAX(0, count - 1), updated_at = ?
                    WHERE usage_date = ? AND account_id = ?
                    """,
                    (self._now(), claim_date, legacy_claim['account_id']),
                )
            legacy_claims = connection.execute(
                """
                SELECT company_key, claimed_at
                FROM company_deliveries
                WHERE claimed_at_epoch <= 0
                """
            ).fetchall()
            for legacy_claim in legacy_claims:
                try:
                    claimed_at_epoch = datetime.fromisoformat(
                        str(legacy_claim['claimed_at']),
                    ).timestamp()
                except (OSError, OverflowError, TypeError, ValueError):
                    claimed_at_epoch = 0
                if claimed_at_epoch > 0:
                    connection.execute(
                        """
                        UPDATE company_deliveries
                        SET claimed_at_epoch = ?
                        WHERE company_key = ?
                        """,
                        (claimed_at_epoch, legacy_claim['company_key']),
                    )
            connection.execute(
                """
                UPDATE company_deliveries
                SET lease_expires_at = claimed_at_epoch + ?
                WHERE status = 'reserved'
                  AND lease_expires_at <= 0
                  AND claimed_at_epoch > 0
                """,
                (RESERVED_LEASE_SECONDS,),
            )
            connection.execute(
                'CREATE INDEX IF NOT EXISTS idx_company_deliveries_claimed_epoch '
                'ON company_deliveries(account_id, claimed_at_epoch)'
            )
            connection.execute(
                'CREATE INDEX IF NOT EXISTS idx_ai_evaluations_lease '
                'ON ai_evaluations(status, lease_expires_at)'
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
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        columns = {
            row['name']
            for row in connection.execute(f'PRAGMA table_info({table})').fetchall()
        }
        if column not in columns:
            connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec='seconds')

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime('%Y-%m-%d')

    @staticmethod
    def _now_epoch() -> float:
        return time.time()

    @staticmethod
    def _reclaim_expired_reserved(
        connection: sqlite3.Connection,
        now_epoch: float,
        now_text: str,
    ) -> int:
        rows = connection.execute(
            """
            SELECT claim_token, account_id, claimed_at
            FROM company_deliveries
            WHERE status = 'reserved'
              AND lease_expires_at > 0
              AND lease_expires_at <= ?
            """,
            (now_epoch,),
        ).fetchall()
        for row in rows:
            claim_date = (row['claimed_at'] or '')[:10]
            connection.execute(
                'DELETE FROM company_deliveries WHERE claim_token = ? AND status = \'reserved\'',
                (row['claim_token'],),
            )
            if claim_date:
                connection.execute(
                    """
                    UPDATE daily_account_usage
                    SET count = MAX(0, count - 1), updated_at = ?
                    WHERE usage_date = ? AND account_id = ?
                    """,
                    (now_text, claim_date, row['account_id']),
                )
        return len(rows)

    def _gate_result(
        self,
        connection: sqlite3.Connection,
        *,
        company_key: str,
        account_id: str,
        daily_limit: int,
        hourly_limit: int,
        daily_target: int,
        min_interval_ms: int,
        now_epoch: float,
        policy_reason: str = '',
    ) -> dict:
        existing = connection.execute(
            """
            SELECT company, title, account_id, worker_id, status, claimed_at
            FROM company_deliveries WHERE company_key = ?
            """,
            (company_key,),
        ).fetchone()
        if existing:
            return {
                'allowed': False,
                'reason': 'duplicate_job',
                'existing': dict(existing),
            }
        if policy_reason:
            return {'allowed': False, 'reason': str(policy_reason)[:120]}

        usage = connection.execute(
            'SELECT count FROM daily_account_usage WHERE usage_date = ? AND account_id = ?',
            (self._today(), account_id),
        ).fetchone()
        count = int(usage['count']) if usage else 0
        if count >= daily_limit:
            return {
                'allowed': False,
                'reason': 'daily_limit',
                'count': count,
                'limit': daily_limit,
                'remaining': 0,
            }
        if daily_target > 0 and count >= daily_target:
            return {
                'allowed': False,
                'reason': 'daily_target',
                'count': count,
                'limit': daily_target,
                'remaining': 0,
            }
        if hourly_limit > 0:
            hourly_count = int(connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM company_deliveries
                WHERE account_id = ? AND claimed_at_epoch >= ?
                """,
                (account_id, now_epoch - 3600),
            ).fetchone()['count'])
            if hourly_count >= hourly_limit:
                return {
                    'allowed': False,
                    'reason': 'hourly_limit',
                    'count': hourly_count,
                    'limit': hourly_limit,
                    'remaining': 0,
                }
        if min_interval_ms > 0:
            latest = connection.execute(
                'SELECT MAX(claimed_at_epoch) AS claimed_at FROM company_deliveries WHERE account_id = ?',
                (account_id,),
            ).fetchone()['claimed_at']
            retry_after_ms = max(0, int(float(latest or 0) * 1000 + min_interval_ms - now_epoch * 1000))
            if latest and retry_after_ms > 0:
                return {
                    'allowed': False,
                    'reason': 'minimum_interval',
                    'retryAfterMs': retry_after_ms,
                }
        return {
            'allowed': True,
            'reason': 'qualified',
            'count': count,
            'limit': daily_limit,
            'remaining': max(0, daily_limit - count),
        }

    def preflight(
        self,
        *,
        company: str,
        title: str,
        account_id: str,
        daily_limit: int | None = None,
        hourly_limit: int = 0,
        daily_target: int = 0,
        min_interval_ms: int = 0,
    ) -> dict:
        """Recheck duplicate and quota policy without reserving a delivery."""
        company_key = delivery_key(company, title)
        account_id = (account_id or '').strip()
        if not company_key:
            return {'allowed': False, 'reason': 'missing_company'}
        if not normalize_title(title):
            return {'allowed': False, 'reason': 'missing_title'}
        if not account_id:
            return {'allowed': False, 'reason': 'missing_account_id'}
        effective_limit = self.daily_limit if daily_limit is None else max(1, int(daily_limit))
        now_epoch = self._now_epoch()
        now_text = self._now()
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            self._reclaim_expired_reserved(connection, now_epoch, now_text)
            result = self._gate_result(
                connection,
                company_key=company_key,
                account_id=account_id,
                daily_limit=effective_limit,
                hourly_limit=max(0, int(hourly_limit or 0)),
                daily_target=max(0, int(daily_target or 0)),
                min_interval_ms=max(0, int(min_interval_ms or 0)),
                now_epoch=now_epoch,
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

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
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            self._reclaim_expired_reserved(connection, self._now_epoch(), self._now())
            row = connection.execute(
                'SELECT count FROM daily_account_usage WHERE usage_date = ? AND account_id = ?',
                (self._today(), account_id),
            ).fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        count = int(row['count']) if row else 0
        return {
            'date': self._today(),
            'accountId': account_id,
            'count': count,
            'limit': self.daily_limit,
            'remaining': max(0, self.daily_limit - count),
            'reached': count >= self.daily_limit,
        }

    def policy_status(
        self,
        account_id: str,
        *,
        daily_limit: int,
        daily_target: int = 0,
        hourly_limit: int = 0,
        min_interval_ms: int = 0,
    ) -> dict:
        """Evaluate startup quota gates using the same limits as qualify and claim."""
        account_id = (account_id or '').strip()
        daily_limit = max(0, int(daily_limit or 0))
        daily_target = max(0, int(daily_target or 0))
        hourly_limit = max(0, int(hourly_limit or 0))
        min_interval_ms = max(0, int(min_interval_ms or 0))
        now_epoch = self._now_epoch()
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            self._reclaim_expired_reserved(connection, now_epoch, self._now())
            usage = connection.execute(
                """
                SELECT count FROM daily_account_usage
                WHERE usage_date = ? AND account_id = ?
                """,
                (self._today(), account_id),
            ).fetchone()
            count = int(usage['count']) if usage else 0
            hourly_count = int(connection.execute(
                """
                SELECT COUNT(*) AS count FROM company_deliveries
                WHERE account_id = ? AND claimed_at_epoch >= ?
                """,
                (account_id, now_epoch - 3600),
            ).fetchone()['count'])
            latest = connection.execute(
                """
                SELECT MAX(claimed_at_epoch) AS claimed_at
                FROM company_deliveries WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()['claimed_at']
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        retry_after_ms = max(
            0,
            int(float(latest or 0) * 1000 + min_interval_ms - now_epoch * 1000),
        )
        reason = ''
        if count >= daily_limit:
            reason = 'daily_limit'
        elif daily_target > 0 and count >= daily_target:
            reason = 'daily_target'
        elif hourly_limit > 0 and hourly_count >= hourly_limit:
            reason = 'hourly_limit'
        elif latest and min_interval_ms > 0 and retry_after_ms > 0:
            reason = 'minimum_interval'
        effective_limit = (
            min(daily_limit, daily_target) if daily_target > 0 else daily_limit
        )
        return {
            'success': True,
            'allowed': not reason,
            'reason': reason or 'available',
            'date': self._today(),
            'accountId': account_id,
            'count': count,
            'limit': effective_limit,
            'dailyLimit': daily_limit,
            'dailyTarget': daily_target,
            'hourlyCount': hourly_count,
            'hourlyLimit': hourly_limit,
            'minIntervalMs': min_interval_ms,
            'retryAfterMs': retry_after_ms,
            'remaining': max(0, effective_limit - count),
            'reached': bool(reason),
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
        qualification_fingerprint: str = '',
        hourly_limit: int = 0,
        daily_target: int = 0,
        min_interval_ms: int = 0,
        lease_seconds: int = RESERVED_LEASE_SECONDS,
        policy_reason: str = '',
        ai_fingerprint: str = '',
        ai_required: bool = False,
        policy_refresh: Callable[[], dict] | None = None,
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
        qualification_fingerprint = (qualification_fingerprint or '').strip()
        company_key = delivery_key(company, title)
        if not company_key:
            return {'accepted': False, 'reason': 'missing_company'}
        if not normalize_title(title):
            return {'accepted': False, 'reason': 'missing_title'}
        if not account_id:
            return {'accepted': False, 'reason': 'missing_account_id'}
        if not worker_id:
            return {'accepted': False, 'reason': 'missing_worker_id'}
        if not qualification_fingerprint:
            return {'accepted': False, 'reason': 'missing_qualification_fingerprint'}

        effective_limit = self.daily_limit if daily_limit is None else max(0, int(daily_limit))
        lease_seconds = max(1, int(lease_seconds or RESERVED_LEASE_SECONDS))
        claim_token = secrets.token_urlsafe(24)
        connection = self._connect()
        try:
            # 在读取重复记录和额度前取得写锁，使后续检查与两项写入构成一个原子操作。
            connection.execute('BEGIN IMMEDIATE')
            today = self._today()
            now = self._now()
            now_epoch = self._now_epoch()
            lease_expires_at = now_epoch + lease_seconds
            self._reclaim_expired_reserved(connection, now_epoch, now)

            if policy_refresh is not None:
                try:
                    refreshed = policy_refresh()
                    if not isinstance(refreshed, dict):
                        raise TypeError('policy refresh must return a dict')
                    policy_reason = str(refreshed.get('policy_reason') or '')
                    effective_limit = max(
                        0,
                        int(refreshed.get('daily_limit', effective_limit)),
                    )
                    hourly_limit = max(
                        0,
                        int(refreshed.get('hourly_limit', hourly_limit) or 0),
                    )
                    daily_target = max(
                        0,
                        int(refreshed.get('daily_target', daily_target) or 0),
                    )
                    min_interval_ms = max(
                        0,
                        int(refreshed.get('min_interval_ms', min_interval_ms) or 0),
                    )
                except Exception:
                    connection.rollback()
                    return {
                        'success': False,
                        'allowed': False,
                        'accepted': False,
                        'reason': 'policy_refresh_failed',
                    }

            existing_claim = connection.execute(
                """
                SELECT account_id, worker_id, claim_token, status,
                       qualification_fingerprint, ai_fingerprint, ai_required,
                       lease_expires_at
                FROM company_deliveries
                WHERE company_key = ?
                """,
                (company_key,),
            ).fetchone()
            if (
                existing_claim
                and existing_claim['status'] == 'reserved'
                and existing_claim['account_id'] == account_id
                and existing_claim['worker_id'] == worker_id
                and existing_claim['qualification_fingerprint']
                == qualification_fingerprint
            ):
                if policy_reason:
                    connection.rollback()
                    return {
                        'success': False,
                        'allowed': False,
                        'accepted': False,
                        'reason': str(policy_reason)[:120],
                    }
                connection.execute(
                    'UPDATE company_deliveries SET lease_expires_at = ? WHERE claim_token = ?',
                    (lease_expires_at, existing_claim['claim_token']),
                )
                usage = connection.execute(
                    """
                    SELECT count FROM daily_account_usage
                    WHERE usage_date = ? AND account_id = ?
                    """,
                    (today, account_id),
                ).fetchone()
                count = int(usage['count']) if usage else 0
                connection.commit()
                return {
                    'success': True,
                    'allowed': True,
                    'accepted': True,
                    'reason': 'claimed',
                    'idempotent': True,
                    'claimToken': existing_claim['claim_token'],
                    'companyKey': company_key,
                    'qualificationFingerprint': existing_claim['qualification_fingerprint'],
                    'aiFingerprint': existing_claim['ai_fingerprint'],
                    'aiRequired': bool(existing_claim['ai_required']),
                    'leaseExpiresAt': lease_expires_at,
                    'count': count,
                    'limit': effective_limit,
                    'remaining': max(0, effective_limit - count),
                }

            connection.execute(
                """
                INSERT INTO daily_account_usage(usage_date, account_id, count, updated_at)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(usage_date, account_id) DO NOTHING
                """,
                (today, account_id, now),
            )
            gate = self._gate_result(
                connection,
                company_key=company_key,
                account_id=account_id,
                daily_limit=effective_limit,
                hourly_limit=max(0, int(hourly_limit or 0)),
                daily_target=max(0, int(daily_target or 0)),
                min_interval_ms=max(0, int(min_interval_ms or 0)),
                now_epoch=now_epoch,
                policy_reason=(policy_reason or '').strip(),
            )
            if not gate['allowed']:
                connection.rollback()
                return {
                    **gate,
                    'accepted': False,
                }
            count = int(gate['count'])

            connection.execute(
                """
                INSERT INTO company_deliveries(
                    company_key, company, title, job_url, account_id, worker_id,
                    claim_token, qualification_fingerprint, status, claimed_at,
                    ai_fingerprint, ai_required, claimed_at_epoch, lease_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?, ?, ?)
                """,
                (
                    company_key,
                    company,
                    title,
                    job_url or '',
                    account_id,
                    worker_id,
                    claim_token,
                    qualification_fingerprint,
                    now,
                    (ai_fingerprint or '').strip(),
                    int(bool(ai_required)),
                    now_epoch,
                    lease_expires_at,
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
                'success': True,
                'allowed': True,
                'accepted': True,
                'reason': 'claimed',
                'claimToken': claim_token,
                'companyKey': company_key,
                'qualificationFingerprint': qualification_fingerprint,
                'aiFingerprint': (ai_fingerprint or '').strip(),
                'aiRequired': bool(ai_required),
                'leaseExpiresAt': lease_expires_at,
                'count': count + 1,
                'limit': effective_limit,
                'remaining': max(0, effective_limit - count - 1),
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def renew(
        self,
        claim_token: str,
        *,
        worker_id: str,
        lease_seconds: int = RESERVED_LEASE_SECONDS,
    ) -> dict:
        """Renew a live reserved claim; queued and terminal records never need renewal."""
        claim_token = (claim_token or '').strip()
        worker_id = (worker_id or '').strip()
        now_epoch = self._now_epoch()
        now_text = self._now()
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            self._reclaim_expired_reserved(connection, now_epoch, now_text)
            row = connection.execute(
                'SELECT status, worker_id FROM company_deliveries WHERE claim_token = ?',
                (claim_token,),
            ).fetchone()
            if not row:
                connection.rollback()
                return {'success': False, 'reason': 'claim_not_found'}
            if row['status'] != 'reserved':
                connection.rollback()
                return {
                    'success': False,
                    'reason': 'claim_not_reserved',
                    'status': row['status'],
                }
            if not worker_id or row['worker_id'] != worker_id:
                connection.rollback()
                return {'success': False, 'reason': 'claim_owner_mismatch'}
            lease_expires_at = now_epoch + max(1, int(lease_seconds or RESERVED_LEASE_SECONDS))
            connection.execute(
                'UPDATE company_deliveries SET lease_expires_at = ? WHERE claim_token = ?',
                (lease_expires_at, claim_token),
            )
            connection.commit()
            return {
                'success': True,
                'reason': 'renewed',
                'claimToken': claim_token,
                'leaseExpiresAt': lease_expires_at,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark(
        self,
        claim_token: str,
        status: str,
        error: str = '',
        *,
        policy_check: Callable[[dict], str] | None = None,
    ) -> dict:
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
            self._reclaim_expired_reserved(connection, self._now_epoch(), now)
            row = connection.execute(
                """
                SELECT company, title, account_id, worker_id, status,
                       qualification_fingerprint, ai_fingerprint, ai_required
                FROM company_deliveries WHERE claim_token = ?
                """,
                (claim_token,),
            ).fetchone()
            if not row:
                connection.rollback()
                return {'success': False, 'reason': 'claim_not_found'}

            identity = {'company': row['company'], 'title': row['title']}
            current = row['status']
            if status == 'queued' and not row['qualification_fingerprint']:
                connection.rollback()
                return {
                    'success': False,
                    'reason': 'qualification_required',
                    'status': current,
                    **identity,
                }
            if (
                status == 'queued'
                and current in {'reserved', 'queued'}
                and policy_check is not None
            ):
                try:
                    policy_reason = str(policy_check(dict(row)) or '')
                except Exception:
                    policy_reason = 'policy_refresh_failed'
                if policy_reason:
                    connection.rollback()
                    return {
                        'success': False,
                        'reason': policy_reason[:120],
                        'status': current,
                        **identity,
                    }
            if current == status:
                connection.rollback()
                return {
                    'success': True,
                    'status': current,
                    'idempotent': True,
                    **identity,
                }
            if current == 'reserved' and status == 'queued' and bool(row['ai_required']):
                decision = connection.execute(
                    'SELECT passed FROM ai_decisions WHERE ai_fingerprint = ?',
                    (row['ai_fingerprint'],),
                ).fetchone()
                if not decision or not bool(decision['passed']):
                    connection.rollback()
                    return {
                        'success': False,
                        'reason': 'ai_not_approved',
                        'status': current,
                        **identity,
                    }
            valid_transition = (
                (current == 'reserved' and status == 'queued')
                or (current == 'queued' and status in FINAL_STATUSES)
            )
            if not valid_transition:
                connection.rollback()
                return {
                    'success': False,
                    'reason': 'invalid_transition',
                    'status': current,
                    'requestedStatus': status,
                    **identity,
                }

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
            return {
                'success': True,
                'status': status,
                'idempotent': False,
                **identity,
            }
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
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            self._reclaim_expired_reserved(connection, self._now_epoch(), self._now())
            row = connection.execute(
                """
                SELECT company, title, account_id, worker_id, status, claimed_at,
                       qualification_fingerprint, ai_fingerprint, ai_required,
                       lease_expires_at,
                       queued_at, completed_at
                FROM company_deliveries
                WHERE claim_token = ?
                """,
                (claim_token,),
            ).fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return {'exists': bool(row), 'delivery': dict(row) if row else None}

    def skip_ai_requirement(self, claim_token: str) -> dict:
        """Atomically relax a reserved claim after the AI filter is disabled."""
        claim_token = (claim_token or '').strip()
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            self._reclaim_expired_reserved(connection, self._now_epoch(), self._now())
            row = connection.execute(
                """
                SELECT company, title, status, ai_required
                FROM company_deliveries WHERE claim_token = ?
                """,
                (claim_token,),
            ).fetchone()
            if not row:
                connection.rollback()
                return {'success': False, 'reason': 'claim_not_found'}
            if row['status'] != 'reserved':
                connection.rollback()
                return {
                    'success': False,
                    'reason': 'claim_not_reserved',
                    'status': row['status'],
                }
            idempotent = not bool(row['ai_required'])
            connection.execute(
                'UPDATE company_deliveries SET ai_required = 0 WHERE claim_token = ?',
                (claim_token,),
            )
            connection.commit()
            return {
                'success': True,
                'reason': 'ai_requirement_skipped',
                'idempotent': idempotent,
                'company': row['company'],
                'title': row['title'],
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

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
            self._reclaim_expired_reserved(connection, self._now_epoch(), self._now())
            row = connection.execute(
                """
                SELECT company, title, account_id, status, claimed_at
                FROM company_deliveries WHERE claim_token = ?
                """,
                (claim_token,),
            ).fetchone()
            if not row:
                connection.rollback()
                return {'success': False, 'reason': 'claim_not_found'}
            if row['status'] != 'reserved':
                connection.rollback()
                return {
                    'success': False,
                    'reason': 'already_started',
                    'status': row['status'],
                    'company': row['company'],
                    'title': row['title'],
                }

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
            return {
                'success': True,
                'released': True,
                'reason': (reason or '')[:1000],
                'company': row['company'],
                'title': row['title'],
            }
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
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            self._reclaim_expired_reserved(connection, self._now_epoch(), self._now())
            row = connection.execute(
                """
                SELECT company, title, account_id, worker_id, status, claimed_at,
                       qualification_fingerprint, lease_expires_at,
                       queued_at, completed_at
                FROM company_deliveries
                WHERE company_key = ?
                ORDER BY claimed_at DESC LIMIT 1
                """,
                (company_key,),
            ).fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return {'exists': bool(row), 'delivery': dict(row) if row else None}

    def get_ai_decision(self, ai_fingerprint: str) -> dict | None:
        """Return a reliable persistent AI pass/reject decision by full context hash."""
        ai_fingerprint = (ai_fingerprint or '').strip()
        if not ai_fingerprint:
            return None
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT ai_fingerprint, passed, reason, decided_at
                FROM ai_decisions WHERE ai_fingerprint = ?
                """,
                (ai_fingerprint,),
            ).fetchone()
        if not row:
            return None
        return {
            'aiFingerprint': row['ai_fingerprint'],
            'passed': bool(row['passed']),
            'reason': row['reason'],
            'reliable': True,
            'decidedAt': row['decided_at'],
        }

    def save_ai_decision(
        self,
        ai_fingerprint: str,
        passed: bool,
        reason: str,
        *,
        reliable: bool,
    ) -> bool:
        """Persist only reliable semantic decisions; infrastructure failures are skipped."""
        ai_fingerprint = (ai_fingerprint or '').strip()
        if not reliable or not ai_fingerprint:
            return False
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO ai_decisions(ai_fingerprint, passed, reason, decided_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(ai_fingerprint) DO UPDATE SET
                    passed = excluded.passed,
                    reason = excluded.reason,
                    decided_at = excluded.decided_at
                """,
                (ai_fingerprint, int(bool(passed)), (reason or '')[:2000], self._now()),
            )
        return True

    def start_ai_evaluation(
        self,
        ai_fingerprint: str,
        owner_id: str,
        *,
        lease_seconds: int = AI_EVALUATION_LEASE_SECONDS,
        lease_scope: str = '',
    ) -> dict:
        """Acquire one fingerprint evaluation or join its still-live pending lease."""
        ai_fingerprint = (ai_fingerprint or '').strip()
        owner_id = (owner_id or '').strip()
        if not ai_fingerprint:
            raise ValueError('missing_ai_fingerprint')
        if not owner_id:
            raise ValueError('missing_evaluation_owner')
        lease_scope = (lease_scope or '').strip()
        evaluation_fingerprint = (
            f'{ai_fingerprint}\x1f{lease_scope}' if lease_scope else ai_fingerprint
        )
        now_epoch = self._now_epoch()
        now_text = self._now()
        lease_expires_at = now_epoch + max(1, int(lease_seconds or AI_EVALUATION_LEASE_SECONDS))
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            cached = connection.execute(
                'SELECT passed, reason, decided_at FROM ai_decisions WHERE ai_fingerprint = ?',
                (ai_fingerprint,),
            ).fetchone()
            if cached:
                connection.rollback()
                return {
                    'acquired': False,
                    'cached': True,
                    'status': 'completed',
                    'passed': bool(cached['passed']),
                    'reason': cached['reason'],
                    'reliable': True,
                }
            existing = connection.execute(
                """
                SELECT evaluation_id, owner_id, status, passed, reason, reliable,
                       cached, lease_expires_at
                FROM ai_evaluations WHERE ai_fingerprint = ?
                """,
                (evaluation_fingerprint,),
            ).fetchone()
            if existing and existing['status'] == 'pending' and float(existing['lease_expires_at']) > now_epoch:
                connection.rollback()
                return {
                    'acquired': False,
                    'cached': False,
                    'evaluationId': existing['evaluation_id'],
                    'status': 'pending',
                    'leaseExpiresAt': float(existing['lease_expires_at']),
                }
            if existing:
                connection.execute(
                    'DELETE FROM ai_evaluations WHERE evaluation_id = ?',
                    (existing['evaluation_id'],),
                )
            evaluation_id = f'eval-{secrets.token_urlsafe(18)}'
            connection.execute(
                """
                INSERT INTO ai_evaluations(
                    evaluation_id, ai_fingerprint, cache_fingerprint, owner_id, status,
                    lease_expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    evaluation_id,
                    evaluation_fingerprint,
                    ai_fingerprint,
                    owner_id,
                    lease_expires_at,
                    now_text,
                    now_text,
                ),
            )
            connection.commit()
            return {
                'acquired': True,
                'cached': False,
                'evaluationId': evaluation_id,
                'status': 'pending',
                'leaseExpiresAt': lease_expires_at,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def renew_ai_evaluation(
        self,
        evaluation_id: str,
        owner_id: str,
        *,
        lease_seconds: int = AI_EVALUATION_LEASE_SECONDS,
    ) -> dict:
        """Extend a pending evaluation lease while its owner is still running the LLM."""
        evaluation_id = (evaluation_id or '').strip()
        owner_id = (owner_id or '').strip()
        now_epoch = self._now_epoch()
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT owner_id, status, lease_expires_at FROM ai_evaluations WHERE evaluation_id = ?',
                (evaluation_id,),
            ).fetchone()
            if not row:
                connection.rollback()
                return {'success': False, 'reason': 'evaluation_not_found'}
            if row['owner_id'] != owner_id:
                connection.rollback()
                return {'success': False, 'reason': 'evaluation_owner_mismatch'}
            if row['status'] != 'pending':
                connection.rollback()
                return {'success': False, 'reason': 'evaluation_completed'}
            if float(row['lease_expires_at']) <= now_epoch:
                connection.rollback()
                return {'success': False, 'reason': 'evaluation_lease_expired'}
            lease_expires_at = now_epoch + max(1, int(lease_seconds or AI_EVALUATION_LEASE_SECONDS))
            connection.execute(
                'UPDATE ai_evaluations SET lease_expires_at = ?, updated_at = ? WHERE evaluation_id = ?',
                (lease_expires_at, self._now(), evaluation_id),
            )
            connection.commit()
            return {
                'success': True,
                'reason': 'renewed',
                'evaluationId': evaluation_id,
                'leaseExpiresAt': lease_expires_at,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def abandon_ai_evaluation(self, evaluation_id: str, owner_id: str) -> dict:
        """Remove an owned pending evaluation so another worker can acquire it immediately."""
        evaluation_id = (evaluation_id or '').strip()
        owner_id = (owner_id or '').strip()
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT owner_id, status FROM ai_evaluations WHERE evaluation_id = ?',
                (evaluation_id,),
            ).fetchone()
            if not row:
                connection.rollback()
                return {'success': False, 'reason': 'evaluation_not_found'}
            if row['owner_id'] != owner_id:
                connection.rollback()
                return {'success': False, 'reason': 'evaluation_owner_mismatch'}
            if row['status'] != 'pending':
                connection.rollback()
                return {
                    'success': True,
                    'reason': 'evaluation_completed',
                    'idempotent': True,
                }
            connection.execute(
                'DELETE FROM ai_evaluations WHERE evaluation_id = ?',
                (evaluation_id,),
            )
            connection.commit()
            return {
                'success': True,
                'reason': 'evaluation_abandoned',
                'idempotent': False,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_ai_evaluation(
        self,
        evaluation_id: str,
        *,
        passed: bool,
        reason: str,
        reliable: bool,
    ) -> dict:
        """Finish an evaluation and atomically cache it only when the verdict is reliable."""
        evaluation_id = (evaluation_id or '').strip()
        now_epoch = self._now_epoch()
        now_text = self._now()
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                """
                SELECT ai_fingerprint, cache_fingerprint, status, lease_expires_at
                FROM ai_evaluations WHERE evaluation_id = ?
                """,
                (evaluation_id,),
            ).fetchone()
            if not row:
                connection.rollback()
                return {'success': False, 'reason': 'evaluation_not_found'}
            if row['status'] == 'completed':
                connection.rollback()
                return {'success': True, 'reason': 'completed', 'idempotent': True}
            if float(row['lease_expires_at']) <= now_epoch:
                connection.rollback()
                return {'success': False, 'reason': 'evaluation_lease_expired'}
            clean_reason = (reason or '')[:2000]
            connection.execute(
                """
                UPDATE ai_evaluations
                SET status = 'completed', passed = ?, reason = ?, reliable = ?, updated_at = ?
                WHERE evaluation_id = ?
                """,
                (int(bool(passed)), clean_reason, int(bool(reliable)), now_text, evaluation_id),
            )
            if reliable:
                connection.execute(
                    """
                    INSERT INTO ai_decisions(ai_fingerprint, passed, reason, decided_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(ai_fingerprint) DO UPDATE SET
                        passed = excluded.passed,
                        reason = excluded.reason,
                        decided_at = excluded.decided_at
                    """,
                    (
                        row['cache_fingerprint'] or row['ai_fingerprint'],
                        int(bool(passed)),
                        clean_reason,
                        now_text,
                    ),
                )
            connection.commit()
            return {
                'success': True,
                'reason': 'completed',
                'evaluationId': evaluation_id,
                'passed': bool(passed),
                'reliable': bool(reliable),
                'idempotent': False,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_ai_evaluation(self, evaluation_id: str) -> dict | None:
        """Read one pending or completed evaluation for the status endpoint."""
        evaluation_id = (evaluation_id or '').strip()
        if not evaluation_id:
            return None
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT evaluation_id, ai_fingerprint, cache_fingerprint, status, passed, reason,
                       reliable, cached, lease_expires_at, created_at, updated_at
                FROM ai_evaluations WHERE evaluation_id = ?
                """,
                (evaluation_id,),
            ).fetchone()
        if not row:
            return None
        result = {
            'evaluationId': row['evaluation_id'],
            'aiFingerprint': row['cache_fingerprint'] or row['ai_fingerprint'],
            'status': row['status'],
            'passed': bool(row['passed']) if row['passed'] is not None else None,
            'reason': row['reason'],
            'reliable': bool(row['reliable']),
            'cached': bool(row['cached']),
            'leaseExpiresAt': float(row['lease_expires_at']),
            'createdAt': row['created_at'],
            'updatedAt': row['updated_at'],
        }
        if result['status'] == 'pending' and result['leaseExpiresAt'] <= self._now_epoch():
            result['status'] = 'expired'
            result['reason'] = 'evaluation_lease_expired'
        return result

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
