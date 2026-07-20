from __future__ import annotations

import json
from pathlib import Path
import unittest
import uuid

from app.storage.dashboard_data import load_dashboard_data
from app.storage.delivery_store import DeliveryStore


class DashboardDataTests(unittest.TestCase):
    def setUp(self) -> None:
        token = uuid.uuid4().hex
        self.action_log_path = Path(__file__).parent / f'.test_dashboard_data_{token}.jsonl'
        self.database_path = Path(__file__).parent / f'.test_dashboard_data_{token}.db'
        self.delivery_store = DeliveryStore(self.database_path)

    def tearDown(self) -> None:
        self.action_log_path.unlink(missing_ok=True)
        for suffix in ('', '-wal', '-shm'):
            Path(f'{self.database_path}{suffix}').unlink(missing_ok=True)

    def write_actions(self, actions: list[dict]) -> None:
        content = ''.join(json.dumps(action, ensure_ascii=False) + '\n' for action in actions)
        self.action_log_path.write_text(content, encoding='utf-8')

    def test_evaluated_jobs_are_grouped_by_local_date(self) -> None:
        self.write_actions([
            {'action': 'job_decision_consumed', 'loggedAt': '2026-07-15T23:59:59'},
            {'action': 'job_decision_consumed', 'loggedAt': '2026-07-16T00:00:00'},
            {'action': 'job_decision_consumed', 'loggedAt': '2026-07-16T18:30:00'},
            {'action': 'job_decision_consumed', 'loggedAt': 'not-a-timestamp'},
            {'action': 'greet_queue_failed', 'loggedAt': '2026-07-16T19:00:00'},
        ])

        summary = load_dashboard_data(self.action_log_path, self.delivery_store)['summary']

        self.assertEqual(summary['evaluatedJobs'], 4)
        self.assertEqual(summary['evaluatedJobsByDate'], [
            {'date': '2026-07-15', 'count': 1},
            {'date': '2026-07-16', 'count': 2},
        ])
        self.assertEqual(summary['undatedEvaluatedJobs'], 1)
        self.assertEqual(
            summary['evaluatedJobs'],
            sum(item['count'] for item in summary['evaluatedJobsByDate'])
            + summary['undatedEvaluatedJobs'],
        )

    def test_empty_dashboard_keeps_evaluation_series_shape(self) -> None:
        summary = load_dashboard_data(self.action_log_path, self.delivery_store)['summary']

        self.assertEqual(summary['evaluatedJobs'], 0)
        self.assertEqual(summary['evaluatedJobsByDate'], [])
        self.assertEqual(summary['undatedEvaluatedJobs'], 0)

    def test_database_only_delivery_is_visible_without_action_log(self) -> None:
        claimed = self.delivery_store.claim(
            company='示例公司',
            title='运维工程师',
            account_id='account-1',
            worker_id='worker-1',
            qualification_fingerprint='qualification-1',
        )

        dashboard = load_dashboard_data(self.action_log_path, self.delivery_store)

        self.assertEqual(dashboard['summary']['totalApplications'], 1)
        record = dashboard['deliveries'][0]
        self.assertEqual(record['claimToken'], claimed['claimToken'])
        self.assertEqual(record['company'], '示例公司')
        self.assertEqual(record['title'], '运维工程师')
        self.assertEqual(record['status'], 'reserved')
        self.assertEqual(record['sourceAction'], 'database')
        self.assertFalse(record['canDelete'])

        self.assertTrue(self.delivery_store.mark(claimed['claimToken'], 'queued')['success'])
        self.assertTrue(
            self.delivery_store.mark(claimed['claimToken'], 'failed_unknown')['success'],
        )
        final_record = load_dashboard_data(
            self.action_log_path,
            self.delivery_store,
        )['deliveries'][0]
        self.assertEqual(final_record['status'], 'failed_unknown')
        self.assertFalse(final_record['canDelete'])


if __name__ == '__main__':
    unittest.main()
