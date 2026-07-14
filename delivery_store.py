from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import re
import secrets
import sqlite3
import unicodedata


FINAL_STATUSES = {'sent', 'failed_unknown'}
ACTIVE_STATUSES = {'reserved', 'queued', *FINAL_STATUSES}


def normalize_company(company: str) -> str:
    """生成稳定的公司唯一键，兼容全半角、空格和常见标点差异。"""
    normalized = unicodedata.normalize('NFKC', company or '').casefold().strip()
    return re.sub(r'[\W_]+', '', normalized, flags=re.UNICODE)


def normalize_title(title: str) -> str:
    """生成稳定的岗位标题键，避免空格、大小写和全半角差异造成漏判。"""
    normalized = unicodedata.normalize('NFKC', title or '').casefold().strip()
    return re.sub(r'[\W_]+', '', normalized, flags=re.UNICODE)


def delivery_key(company: str, title: str) -> str:
    company_key = normalize_company(company)
    if not company_key:
        return ''
    return f'{company_key}\x1f{normalize_title(title)}'


class DeliveryStore:
    """基于 SQLite 的跨线程、跨浏览器投递协调器。"""

    def __init__(self, db_path: Path | str, daily_limit: int = 90):
        self.db_path = Path(db_path)
        self.daily_limit = max(1, int(daily_limit))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
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
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
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
            # Two-phase key rewrite avoids UNIQUE collisions when upgrading a
            # database whose rows still use the old company-only key format.
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
        """兼容旧客户端的原子计数接口。新客户端应使用 claim。"""
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
    ) -> dict:
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
        claim_token = secrets.token_urlsafe(24)
        connection = self._connect()
        try:
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
            if count >= self.daily_limit:
                connection.rollback()
                return {
                    'accepted': False,
                    'reason': 'daily_limit',
                    'count': count,
                    'limit': self.daily_limit,
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
                'limit': self.daily_limit,
                'remaining': max(0, self.daily_limit - count - 1),
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark(self, claim_token: str, status: str, error: str = '') -> dict:
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
        """删除已结束的投递历史；不返还每日额度，避免通过删记录绕过上限。"""
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
        """仅释放确认尚未发起沟通的占位，并返还账号当日名额。"""
        claim_token = (claim_token or '').strip()
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT account_id, status FROM company_deliveries WHERE claim_token = ?',
                (claim_token,),
            ).fetchone()
            if not row:
                connection.rollback()
                return {'success': False, 'reason': 'claim_not_found'}
            if row['status'] != 'reserved':
                connection.rollback()
                return {'success': False, 'reason': 'already_started', 'status': row['status']}

            connection.execute('DELETE FROM company_deliveries WHERE claim_token = ?', (claim_token,))
            connection.execute(
                """
                UPDATE daily_account_usage
                SET count = MAX(0, count - 1), updated_at = ?
                WHERE usage_date = ? AND account_id = ?
                """,
                (self._now(), self._today(), row['account_id']),
            )
            connection.commit()
            return {'success': True, 'released': True, 'reason': (reason or '')[:1000]}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def company_status(self, company: str, title: str = '') -> dict:
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

    def record_legacy_sent(self, company: str, title: str, account_id: str = 'legacy') -> dict:
        """兼容旧客户端：原子写入公司+岗位记录，但不重复占用每日额度。"""
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
        path = Path(path)
        if not path.exists():
            return 0
        imported = 0
        with path.open('r', encoding='utf-8') as file:
            for line in file:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                company = record.get('company') or ''
                if company and not self.record_legacy_sent(company, record.get('title') or '').get('duplicate'):
                    imported += 1
        return imported

    def import_action_log(self, path: Path | str) -> int:
        """从历史动作日志恢复可能已经发起过沟通的公司，采用宁可少投、不重复投。"""
        path = Path(path)
        if not path.exists():
            return 0
        delivery_actions = {
            'greet_queued',
            'greet_sent',
            'chat_greet_sent',
        }
        imported = 0
        with path.open('r', encoding='utf-8') as file:
            for line in file:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get('action') not in delivery_actions:
                    continue
                company = record.get('company') or ''
                if company and not self.record_legacy_sent(company, record.get('title') or '').get('duplicate'):
                    imported += 1
        return imported
