import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading
import uuid

from delivery_store import DeliveryStore, normalize_company, normalize_title


class MemoryDeliveryStore(DeliveryStore):
    def __init__(self, daily_limit=90, initialize=True):
        self.db_path = Path('.')
        self.daily_limit = daily_limit
        self._memory_lock = threading.Lock()
        self._uri = f'file:goodjobs-{uuid.uuid4().hex}?mode=memory&cache=shared'
        self._anchor = sqlite3.connect(self._uri, uri=True, isolation_level=None, check_same_thread=False)
        if initialize:
            self._initialize()

    def _connect(self):
        connection = sqlite3.connect(
            self._uri,
            uri=True,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA busy_timeout = 30000')
        return connection

    def close(self):
        self._anchor.close()

    def claim(self, **kwargs):
        # Shared in-memory SQLite returns SQLITE_LOCKED immediately instead of
        # honoring busy_timeout; serialize only this filesystem-free test double.
        with self._memory_lock:
            return super().claim(**kwargs)


class DeliveryStoreTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryDeliveryStore(daily_limit=2)

    def tearDown(self):
        self.store.close()

    def test_same_company_and_title_is_claimed_only_once_under_concurrency(self):
        def claim(index):
            return self.store.claim(
                company='示例科技（上海）有限公司',
                title='运维工程师',
                account_id=f'account-{index % 4}',
                worker_id=f'worker-{index}',
            )

        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(claim, range(40)))

        accepted = [result for result in results if result['accepted']]
        duplicates = [result for result in results if result.get('reason') == 'duplicate_job']
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(duplicates), 39)

    def test_same_company_with_different_titles_can_both_be_claimed(self):
        first = self.store.claim(company='示例科技', title='运维工程师', account_id='one', worker_id='w1')
        second = self.store.claim(company='示例科技', title='网络工程师', account_id='one', worker_id='w1')

        self.assertTrue(first['accepted'])
        self.assertTrue(second['accepted'])

    def test_duplicate_claim_does_not_increment_daily_usage(self):
        first = self.store.claim(company='示例科技', title='运维工程师', account_id='one', worker_id='w1')
        duplicate = self.store.claim(company='示例科技', title='运维工程师', account_id='one', worker_id='w2')

        self.assertTrue(first['accepted'])
        self.assertEqual(duplicate['reason'], 'duplicate_job')
        self.assertEqual(self.store.quota_status('one')['count'], 1)

    def test_company_normalization_handles_spacing_and_full_width_punctuation(self):
        self.assertEqual(normalize_company(' Foo 科技（上海） '), normalize_company('foo科技(上海)'))

    def test_title_normalization_handles_spacing_and_full_width_punctuation(self):
        self.assertEqual(normalize_title(' SRE（运维）工程师 '), normalize_title('sre(运维) 工程师'))

    def test_old_company_only_key_is_migrated_to_company_and_title(self):
        migrated = MemoryDeliveryStore(daily_limit=5, initialize=False)
        try:
            connection = migrated._anchor
            connection.execute(
                """
                CREATE TABLE company_deliveries (
                    company_key TEXT PRIMARY KEY, company TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
                    job_url TEXT NOT NULL DEFAULT '', account_id TEXT NOT NULL, worker_id TEXT NOT NULL,
                    claim_token TEXT NOT NULL UNIQUE, status TEXT NOT NULL, claimed_at TEXT NOT NULL,
                    queued_at TEXT, completed_at TEXT, last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                INSERT INTO company_deliveries(
                    company_key, company, title, account_id, worker_id, claim_token, status, claimed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ('示例科技', '示例科技', '运维工程师', 'old', 'old-worker', 'old-token', 'sent', '2026-01-01T00:00:00'),
            )
            migrated._initialize()
            self.assertTrue(migrated.company_status('示例科技', '运维工程师')['exists'])
            self.assertFalse(migrated.company_status('示例科技', '网络工程师')['exists'])
        finally:
            migrated.close()

    def test_daily_limit_is_isolated_by_account(self):
        first = self.store.claim(company='甲公司', title='A', account_id='one', worker_id='w1')
        second = self.store.claim(company='乙公司', title='B', account_id='one', worker_id='w1')
        limited = self.store.claim(company='丙公司', title='C', account_id='one', worker_id='w1')
        other = self.store.claim(company='丁公司', title='D', account_id='two', worker_id='w2')

        self.assertTrue(first['accepted'])
        self.assertTrue(second['accepted'])
        self.assertEqual(limited['reason'], 'daily_limit')
        self.assertTrue(other['accepted'])

    def test_daily_limit_cannot_be_exceeded_under_concurrency(self):
        def claim(index):
            return self.store.claim(
                company=f'并发公司-{index}',
                title='工程师',
                account_id='shared-account',
                worker_id=f'worker-{index}',
            )

        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(claim, range(30)))

        accepted = [result for result in results if result['accepted']]
        limited = [result for result in results if result.get('reason') == 'daily_limit']
        self.assertEqual(len(accepted), 2)
        self.assertEqual(len(limited), 28)
        self.assertEqual(self.store.quota_status('shared-account')['count'], 2)

    def test_release_before_queue_restores_quota_and_company(self):
        claim = self.store.claim(company='甲公司', title='A', account_id='one', worker_id='w1')
        status = self.store.claim_status(claim['claimToken'])
        released = self.store.release(claim['claimToken'], 'queue failed')
        retried = self.store.claim(company='甲公司', title='B', account_id='one', worker_id='w2')

        self.assertTrue(status['exists'])
        self.assertEqual(status['delivery']['status'], 'reserved')
        self.assertTrue(released['success'])
        self.assertTrue(retried['accepted'])
        self.assertEqual(self.store.quota_status('one')['count'], 1)

    def test_unknown_result_remains_blocked(self):
        claim = self.store.claim(company='甲公司', title='A', account_id='one', worker_id='w1')
        self.store.mark(claim['claimToken'], 'failed_unknown', 'browser closed')
        release = self.store.release(claim['claimToken'], 'late release')
        duplicate = self.store.claim(company='甲公司', title='A', account_id='two', worker_id='w2')
        different_title = self.store.claim(company='甲公司', title='B', account_id='two', worker_id='w2')

        self.assertEqual(release['reason'], 'already_started')
        self.assertEqual(duplicate['reason'], 'duplicate_job')
        self.assertTrue(different_title['accepted'])

    def test_delete_finished_history_removes_duplicate_block_without_refunding_quota(self):
        claim = self.store.claim(company='甲公司', title='A', account_id='one', worker_id='w1')
        self.store.mark(claim['claimToken'], 'sent')
        deleted = self.store.delete_history([claim['claimToken']], [('甲公司', 'A')])
        retried = self.store.claim(company='甲公司', title='A', account_id='two', worker_id='w2')

        self.assertEqual(deleted['deleted'], 1)
        self.assertTrue(retried['accepted'])
        self.assertEqual(self.store.quota_status('one')['count'], 1)

    def test_delete_active_history_is_rejected(self):
        claim = self.store.claim(company='甲公司', title='A', account_id='one', worker_id='w1')

        with self.assertRaisesRegex(ValueError, '进行中的投递记录不能删除'):
            self.store.delete_history([claim['claimToken']], [('甲公司', 'A')])

        self.assertTrue(self.store.claim_status(claim['claimToken'])['exists'])


if __name__ == '__main__':
    unittest.main()
