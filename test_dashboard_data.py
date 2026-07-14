import json
from pathlib import Path
import unittest

from dashboard_data import delivery_sources, load_dashboard_data, parse_salary_details, parse_salary_k


class DashboardDataTest(unittest.TestCase):
    def test_delivery_source_prefers_stable_event_id(self):
        actions = [
            {'eventId': 'event-1', 'action': 'delivery_claimed', 'claimToken': 'token-1'},
            {'eventId': 'event-2', 'action': 'greet_sent', 'claimToken': 'token-1'},
        ]

        sources = delivery_sources(actions)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['id'], 'event-1')
        self.assertEqual(sources[0]['index'], 0)

    def test_duplicate_skip_is_not_a_delivery_record(self):
        actions = [
            {'eventId': 'event-1', 'action': 'delivery_claimed', 'claimToken': 'token-1'},
            {
                'eventId': 'event-duplicate',
                'action': 'company_duplicate_skipped',
                'company': '示例科技',
                'title': '运维工程师',
            },
        ]

        sources = delivery_sources(actions)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['id'], 'event-1')

    def test_parse_salary_range(self):
        self.assertEqual(parse_salary_k('12-20K'), 16.0)
        self.assertEqual(parse_salary_k('8-12K·13薪'), 10.0)
        self.assertIsNone(parse_salary_k('面议'))

    def test_parse_salary_details_for_advanced_filters(self):
        self.assertEqual(
            parse_salary_details('8-12K·13薪'),
            {'salaryMinK': 8.0, 'salaryMaxK': 12.0, 'salaryK': 10.0, 'salaryMonths': 13},
        )
        self.assertEqual(parse_salary_details('15000-25000元/月')['salaryMaxK'], 25.0)

    def test_loads_legacy_and_current_delivery_actions(self):
        root = Path(__file__).resolve().parent
        action_path = root / '.test-dashboard-actions.jsonl'
        missing_db_path = root / '.test-dashboard-missing.db'
        try:
            rows = [
                {
                    'loggedAt': '2026-07-10T09:30:00',
                    'action': 'greet_queued',
                    'company': '示例科技',
                    'title': 'AI 应用工程师',
                    'salary': '15-25K',
                },
                {
                    'loggedAt': '2026-07-11T10:00:00',
                    'action': 'delivery_claimed',
                    'company': '未来智能',
                    'title': '智能体工程师',
                    'salary': '20-30K',
                    'location': '北京·朝阳区',
                    'claimToken': 'claim-one',
                },
                {
                    'loggedAt': '2026-07-11T10:01:00',
                    'action': 'greet_sent',
                    'claimToken': 'claim-one',
                },
            ]
            action_path.write_text(
                ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows),
                encoding='utf-8',
            )

            result = load_dashboard_data(action_path, missing_db_path)
        finally:
            action_path.unlink(missing_ok=True)
            missing_db_path.unlink(missing_ok=True)

        self.assertEqual(result['summary']['totalApplications'], 2)
        self.assertEqual(result['summary']['uniqueCompanies'], 2)
        self.assertEqual(result['deliveries'][0]['status'], 'sent')
        self.assertEqual(result['deliveries'][0]['location'], '北京·朝阳区')


if __name__ == '__main__':
    unittest.main()
