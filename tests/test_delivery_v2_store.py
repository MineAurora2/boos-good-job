from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import sqlite3
import unittest
import uuid

from app.storage.delivery_store import DeliveryStore


class DeliveryV2StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_id = uuid.uuid4().hex
        self.paths: list[Path] = []
        self.db_path = self.path('delivery')
        self.store = DeliveryStore(self.db_path, daily_limit=20)

    def tearDown(self) -> None:
        for path in self.paths:
            for suffix in ('', '-wal', '-shm'):
                Path(f'{path}{suffix}').unlink(missing_ok=True)

    def path(self, name: str) -> Path:
        path = Path(__file__).parent / f'.test_delivery_v2_{self.test_id}_{name}.db'
        self.paths.append(path)
        return path

    def claim(self, suffix: str, **overrides) -> dict:
        values = {
            'company': f'Company {suffix}',
            'title': f'Role {suffix}',
            'account_id': 'account-1',
            'worker_id': 'worker-1',
            'qualification_fingerprint': f'qualification-{suffix}',
        }
        values.update(overrides)
        return self.store.claim(**values)

    def expire_claim(self, claim_token: str) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                'UPDATE company_deliveries SET lease_expires_at = 1 WHERE claim_token = ?',
                (claim_token,),
            )
            connection.commit()
        finally:
            connection.close()

    def test_claim_persists_qualification_and_renewable_reserved_lease(self) -> None:
        claimed = self.claim('a')
        self.assertTrue(claimed['accepted'])
        self.assertEqual(claimed['qualificationFingerprint'], 'qualification-a')
        original_expiry = claimed['leaseExpiresAt']

        wrong_owner = self.store.renew(
            claimed['claimToken'], worker_id='worker-2', lease_seconds=1800,
        )
        self.assertFalse(wrong_owner['success'])
        self.assertEqual(wrong_owner['reason'], 'claim_owner_mismatch')

        renewed = self.store.renew(
            claimed['claimToken'], worker_id='worker-1', lease_seconds=1800,
        )
        self.assertTrue(renewed['success'])
        self.assertEqual(renewed['reason'], 'renewed')
        self.assertGreater(renewed['leaseExpiresAt'], original_expiry)

        status = self.store.claim_status(claimed['claimToken'])['delivery']
        self.assertEqual(status['qualification_fingerprint'], 'qualification-a')
        self.assertEqual(status['lease_expires_at'], renewed['leaseExpiresAt'])

    def test_claim_requires_v2_qualification_fingerprint(self) -> None:
        denied = self.store.claim(
            company='Acme',
            title='SRE',
            account_id='account-1',
            worker_id='worker-1',
            qualification_fingerprint='',
        )

        self.assertFalse(denied['accepted'])
        self.assertEqual(denied['reason'], 'missing_qualification_fingerprint')

    def test_only_expired_reserved_is_reclaimed_and_quota_is_returned(self) -> None:
        reserved = self.claim('reserved')
        self.expire_claim(reserved['claimToken'])
        self.assertFalse(self.store.claim_status(reserved['claimToken'])['exists'])
        replacement = self.claim('replacement')
        self.assertTrue(replacement['accepted'])
        self.assertEqual(self.store.quota_status('account-1')['count'], 1)

        queued = self.claim('queued')
        self.assertTrue(self.store.mark(queued['claimToken'], 'queued')['success'])
        self.expire_claim(queued['claimToken'])
        self.claim('after-queued')
        queued_status = self.store.claim_status(queued['claimToken'])
        self.assertTrue(queued_status['exists'])
        self.assertEqual(queued_status['delivery']['status'], 'queued')

    def test_state_machine_requires_queued_before_terminal_and_is_idempotent(self) -> None:
        claimed = self.claim('state')
        invalid = self.store.mark(claimed['claimToken'], 'sent')
        self.assertFalse(invalid['success'])
        self.assertEqual(invalid['reason'], 'invalid_transition')

        first_queue = self.store.mark(claimed['claimToken'], 'queued')
        second_queue = self.store.mark(claimed['claimToken'], 'queued')
        self.assertTrue(first_queue['success'])
        self.assertTrue(second_queue['idempotent'])

        sent = self.store.mark(claimed['claimToken'], 'sent')
        terminal_retry = self.store.mark(claimed['claimToken'], 'failed_unknown')
        self.assertTrue(sent['success'])
        self.assertFalse(terminal_retry['success'])
        self.assertEqual(terminal_retry['reason'], 'invalid_transition')
        self.assertEqual(self.store.claim_status(claimed['claimToken'])['delivery']['status'], 'sent')

    def test_ai_required_claim_cannot_queue_until_reliable_pass_is_cached(self) -> None:
        required = self.claim(
            'ai-required', ai_fingerprint='ai-required-fp', ai_required=True,
        )
        before_ai = self.store.mark(required['claimToken'], 'queued')
        self.assertFalse(before_ai['success'])
        self.assertEqual(before_ai['reason'], 'ai_not_approved')

        self.store.save_ai_decision(
            'ai-required-fp', False, 'rejected', reliable=True,
        )
        rejected = self.store.mark(required['claimToken'], 'queued')
        self.assertFalse(rejected['success'])
        self.assertEqual(rejected['reason'], 'ai_not_approved')

        self.store.save_ai_decision(
            'ai-required-fp', True, 'approved', reliable=True,
        )
        approved = self.store.mark(required['claimToken'], 'queued')
        self.assertTrue(approved['success'])

        skipped = self.claim('ai-disabled', ai_required=False)
        self.assertTrue(self.store.mark(skipped['claimToken'], 'queued')['success'])

    def test_preflight_checks_duplicate_before_quota_without_writing(self) -> None:
        store = DeliveryStore(self.path('limit'), daily_limit=1)
        claimed = store.claim(
            company='Acme', title='SRE', account_id='account-1', worker_id='worker-1',
            qualification_fingerprint='fp-1',
        )
        self.assertTrue(claimed['accepted'])
        duplicate = store.preflight(
            company='Acme', title='SRE', account_id='account-1', daily_limit=1,
        )
        self.assertFalse(duplicate['allowed'])
        self.assertEqual(duplicate['reason'], 'duplicate_job')

        limited = store.preflight(
            company='Other', title='Role', account_id='account-1', daily_limit=1,
        )
        self.assertFalse(limited['allowed'])
        self.assertEqual(limited['reason'], 'daily_limit')
        self.assertFalse(store.company_status('Other', 'Role')['exists'])

    def test_claim_rechecks_current_policy_inside_transaction(self) -> None:
        denied = self.claim('policy', policy_reason='sending_disabled')
        self.assertFalse(denied['accepted'])
        self.assertEqual(denied['reason'], 'sending_disabled')
        self.assertFalse(self.store.company_status('Company policy', 'Role policy')['exists'])
        self.assertEqual(self.store.quota_status('account-1')['count'], 0)

    def test_migration_assigns_and_reclaims_expired_legacy_reserved_lease(self) -> None:
        path = self.path('legacy-lease')
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE company_deliveries (
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
                CREATE TABLE daily_account_usage (
                    usage_date TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (usage_date, account_id)
                );
                CREATE TABLE delivery_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )
            connection.execute(
                """
                INSERT INTO company_deliveries(
                    company_key, company, title, account_id, worker_id,
                    claim_token, status, claimed_at
                ) VALUES (?, 'Legacy', 'SRE', 'account-1', 'worker-1',
                          'legacy-reserved', 'reserved', '2000-01-01T00:00:00')
                """,
                ('legacy\x1fsre',),
            )
            connection.execute(
                """
                INSERT INTO daily_account_usage(usage_date, account_id, count, updated_at)
                VALUES ('2000-01-01', 'account-1', 1, '2000-01-01T00:00:00')
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated = DeliveryStore(path)
        self.assertFalse(migrated.claim_status('legacy-reserved')['exists'])
        connection = sqlite3.connect(path)
        try:
            count = connection.execute(
                """
                SELECT count FROM daily_account_usage
                WHERE usage_date = '2000-01-01' AND account_id = 'account-1'
                """
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_migration_interprets_legacy_claimed_at_as_local_time(self) -> None:
        path = self.path('legacy-local-time')
        claimed_at = datetime.now().isoformat(timespec='seconds')
        expected_epoch = datetime.fromisoformat(claimed_at).timestamp()
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE company_deliveries (
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
                CREATE TABLE daily_account_usage (
                    usage_date TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (usage_date, account_id)
                );
                CREATE TABLE delivery_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )
            connection.execute(
                """
                INSERT INTO company_deliveries(
                    company_key, company, title, account_id, worker_id,
                    claim_token, status, claimed_at
                ) VALUES (?, 'Legacy Local', 'SRE', 'account-1', 'worker-1',
                          'legacy-local-time', 'queued', ?)
                """,
                ('legacylocal\x1fsre', claimed_at),
            )
            connection.commit()
        finally:
            connection.close()

        DeliveryStore(path)

        connection = sqlite3.connect(path)
        try:
            migrated_epoch = connection.execute(
                "SELECT claimed_at_epoch FROM company_deliveries WHERE claim_token = ?",
                ('legacy-local-time',),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertLess(abs(migrated_epoch - expected_epoch), 2)

    def test_migration_invalidates_recent_pre_v2_reserved_claim(self) -> None:
        path = self.path('legacy-unqualified-reserved')
        claimed_at = datetime.now().isoformat(timespec='seconds')
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE company_deliveries (
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
                CREATE TABLE daily_account_usage (
                    usage_date TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (usage_date, account_id)
                );
                CREATE TABLE delivery_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )
            connection.execute(
                """
                INSERT INTO company_deliveries(
                    company_key, company, title, account_id, worker_id,
                    claim_token, status, claimed_at
                ) VALUES (?, 'Legacy Active', 'SRE', 'account-1', 'worker-1',
                          'legacy-active', 'reserved', ?)
                """,
                ('legacyactive\x1fsre', claimed_at),
            )
            connection.execute(
                """
                INSERT INTO daily_account_usage(usage_date, account_id, count, updated_at)
                VALUES (?, 'account-1', 1, ?)
                """,
                (claimed_at[:10], claimed_at),
            )
            connection.commit()
        finally:
            connection.close()

        migrated = DeliveryStore(path)

        self.assertFalse(migrated.claim_status('legacy-active')['exists'])
        self.assertEqual(migrated.quota_status('account-1')['count'], 0)

    def test_hourly_limit_is_atomic_across_concurrent_claims(self) -> None:
        barrier_store = DeliveryStore(self.path('hourly'), daily_limit=20)

        def run(index: int) -> dict:
            return barrier_store.claim(
                company=f'Company {index}', title='SRE', account_id='account-1',
                worker_id=f'worker-{index}', qualification_fingerprint=f'fp-{index}',
                hourly_limit=1,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, range(2)))
        self.assertEqual(sum(bool(result['accepted']) for result in results), 1)
        rejected = next(result for result in results if not result['accepted'])
        self.assertEqual(rejected['reason'], 'hourly_limit')

    def test_failed_unknown_counts_for_hourly_limit_and_minimum_interval(self) -> None:
        first = self.claim('failed')
        self.store.mark(first['claimToken'], 'queued')
        self.store.mark(first['claimToken'], 'failed_unknown')

        hourly = self.claim('hourly', hourly_limit=1)
        self.assertFalse(hourly['accepted'])
        self.assertEqual(hourly['reason'], 'hourly_limit')
        interval = self.claim('interval', min_interval_ms=60_000)
        self.assertFalse(interval['accepted'])
        self.assertEqual(interval['reason'], 'minimum_interval')


class AiDecisionLeaseStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(__file__).parent / f'.test_delivery_ai_{uuid.uuid4().hex}.db'
        self.store = DeliveryStore(self.db_path)

    def tearDown(self) -> None:
        for suffix in ('', '-wal', '-shm'):
            Path(f'{self.db_path}{suffix}').unlink(missing_ok=True)

    def test_only_reliable_ai_decisions_are_cached(self) -> None:
        self.assertFalse(self.store.save_ai_decision('ai-1', False, 'transport failed', reliable=False))
        self.assertIsNone(self.store.get_ai_decision('ai-1'))

        self.assertTrue(self.store.save_ai_decision('ai-1', False, 'not suitable', reliable=True))
        cached = self.store.get_ai_decision('ai-1')
        self.assertFalse(cached['passed'])
        self.assertEqual(cached['reason'], 'not suitable')
        self.assertTrue(cached['reliable'])

    def test_evaluation_lease_is_shared_renewable_and_recoverable_after_expiry(self) -> None:
        first = self.store.start_ai_evaluation('ai-2', 'owner-1', lease_seconds=900)
        self.assertTrue(first['acquired'])
        concurrent = self.store.start_ai_evaluation('ai-2', 'owner-2', lease_seconds=900)
        self.assertFalse(concurrent['acquired'])
        self.assertEqual(concurrent['evaluationId'], first['evaluationId'])

        renewed = self.store.renew_ai_evaluation(
            first['evaluationId'], 'owner-1', lease_seconds=1800,
        )
        self.assertTrue(renewed['success'])
        self.assertGreater(renewed['leaseExpiresAt'], first['leaseExpiresAt'])

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                'UPDATE ai_evaluations SET lease_expires_at = 1 WHERE evaluation_id = ?',
                (first['evaluationId'],),
            )
            connection.commit()
        finally:
            connection.close()
        expired = self.store.get_ai_evaluation(first['evaluationId'])
        self.assertEqual(expired['status'], 'expired')
        self.assertEqual(expired['reason'], 'evaluation_lease_expired')
        recovered = self.store.start_ai_evaluation('ai-2', 'owner-2', lease_seconds=900)
        self.assertTrue(recovered['acquired'])
        self.assertNotEqual(recovered['evaluationId'], first['evaluationId'])

    def test_completed_evaluation_persists_status_and_reliable_cache(self) -> None:
        started = self.store.start_ai_evaluation('ai-3', 'owner-1')
        completed = self.store.complete_ai_evaluation(
            started['evaluationId'], passed=True, reason='suitable', reliable=True,
        )
        self.assertTrue(completed['success'])
        status = self.store.get_ai_evaluation(started['evaluationId'])
        self.assertEqual(status['status'], 'completed')
        self.assertTrue(status['passed'])
        self.assertEqual(self.store.get_ai_decision('ai-3')['reason'], 'suitable')

    def test_delivery_claims_have_isolated_leases_but_share_reliable_cache(self) -> None:
        first = self.store.start_ai_evaluation(
            'shared-content',
            'worker-1',
            lease_scope='delivery:claim-a',
        )
        second = self.store.start_ai_evaluation(
            'shared-content',
            'worker-1',
            lease_scope='delivery:claim-b',
        )

        self.assertTrue(first['acquired'])
        self.assertTrue(second['acquired'])
        self.assertNotEqual(first['evaluationId'], second['evaluationId'])

        completed = self.store.complete_ai_evaluation(
            first['evaluationId'],
            passed=True,
            reason='suitable',
            reliable=True,
        )
        self.assertTrue(completed['success'])
        self.assertTrue(self.store.get_ai_decision('shared-content')['passed'])


if __name__ == '__main__':
    unittest.main()
