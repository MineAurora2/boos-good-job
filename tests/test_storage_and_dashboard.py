"""Regression tests for shared persistence and dashboard delivery projection."""

from __future__ import annotations

import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import unittest
import uuid

import admin_store
import config
from dashboard_data import load_dashboard_data
from delivery_store import DeliveryStore
from storage_io import atomic_write_text, read_jsonl, replace_jsonl


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def isolated_directory():
    """Create a writable test directory without tempfile's restrictive Windows ACL."""
    root = ROOT / 'tests' / f'.storage-dashboard-{os.getpid()}-{uuid.uuid4().hex}'
    root.mkdir()
    try:
        yield root
    finally:
        for path in sorted(root.rglob('*'), key=lambda item: len(item.parts), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
        root.rmdir()


class StorageIOTests(unittest.TestCase):
    def test_atomic_write_and_jsonl_reader_share_malformed_line_handling(self):
        with isolated_directory() as directory:
            path = directory / 'events.jsonl'
            atomic_write_text(path, '{"valid": 1}\nnot-json\n')
            with path.open('ab') as file:
                file.write(b'\xff\xfe\n')
            self.assertEqual(read_jsonl(path), [{'valid': 1}])
            self.assertFalse(list(path.parent.glob(f'.{path.name}.*.tmp')))

            replace_jsonl(path, [{'replacement': 'ok'}])
            self.assertEqual(read_jsonl(path), [{'replacement': 'ok'}])


class DashboardProjectionTests(unittest.TestCase):
    def test_database_terminal_state_overrides_queued_source_action(self):
        with isolated_directory() as root:
            store = DeliveryStore(root / 'delivery.db', daily_limit=5)
            claim = store.claim(
                company='示例公司',
                title='运维工程师',
                account_id='account-1',
                worker_id='worker-1',
            )
            store.mark(claim['claimToken'], 'queued')
            store.mark(claim['claimToken'], 'sent')
            replace_jsonl(root / 'actions.jsonl', [{
                'action': 'greet_queued',
                'claimToken': claim['claimToken'],
                'company': '示例公司',
                'title': '运维工程师',
                'loggedAt': '2026-07-15T10:00:00',
            }])

            delivery = load_dashboard_data(root / 'actions.jsonl', store)['deliveries'][0]

            self.assertEqual(delivery['status'], 'sent')
            self.assertTrue(delivery['canDelete'])

    def test_terminal_action_is_visible_without_an_earlier_source_event(self):
        with isolated_directory() as root:
            store = DeliveryStore(root / 'delivery.db')
            replace_jsonl(root / 'actions.jsonl', [{
                'action': 'chat_greet_failed',
                'company': '无令牌公司',
                'title': '平台工程师',
                'loggedAt': '2026-07-15T10:00:00',
            }])

            deliveries = load_dashboard_data(root / 'actions.jsonl', store)['deliveries']

            self.assertEqual(len(deliveries), 1)
            self.assertEqual(deliveries[0]['status'], 'failed_unknown')

    def test_legacy_terminal_action_does_not_duplicate_same_job(self):
        with isolated_directory() as root:
            store = DeliveryStore(root / 'delivery.db')
            replace_jsonl(root / 'actions.jsonl', [
                {
                    'action': 'greet_queued',
                    'company': '历史公司',
                    'title': '运维工程师',
                    'loggedAt': '2026-07-15T10:00:00',
                },
                {
                    'action': 'greet_sent',
                    'company': '历史公司',
                    'title': '运维工程师',
                    'loggedAt': '2026-07-15T10:01:00',
                },
            ])

            deliveries = load_dashboard_data(root / 'actions.jsonl', store)['deliveries']

            self.assertEqual(len(deliveries), 1)
            self.assertEqual(deliveries[0]['status'], 'sent')

    def test_legacy_import_does_not_turn_failed_actions_into_sent_deliveries(self):
        with isolated_directory() as root:
            store = DeliveryStore(root / 'delivery.db')
            replace_jsonl(root / 'actions.jsonl', [
                {
                    'action': 'greet_queue_failed',
                    'company': '失败公司',
                    'title': '运维工程师',
                },
                {
                    'action': 'greet_message_sent',
                    'company': '成功公司',
                    'title': '平台工程师',
                },
            ])

            imported = store.import_action_log(root / 'actions.jsonl')

            self.assertEqual(imported, 1)
            self.assertFalse(store.company_status('失败公司', '运维工程师')['exists'])
            self.assertTrue(store.company_status('成功公司', '平台工程师')['exists'])

    def test_missing_legacy_file_is_not_marked_as_migrated(self):
        with isolated_directory() as root:
            store = DeliveryStore(root / 'delivery.db')
            greeted_path = root / 'greeted.jsonl'
            action_path = root / 'actions.jsonl'
            self.assertEqual(store.import_legacy_once(greeted_path, action_path), 0)

            replace_jsonl(action_path, [{
                'action': 'greet_message_sent',
                'company': '后来恢复的公司',
                'title': '运维工程师',
            }])

            self.assertEqual(store.import_legacy_once(greeted_path, action_path), 1)
            self.assertEqual(store.import_legacy_once(greeted_path, action_path), 0)

    def test_effective_account_limit_is_checked_inside_claim_transaction(self):
        with isolated_directory() as root:
            store = DeliveryStore(root / 'delivery.db', daily_limit=10)

            first = store.claim(
                company='公司一',
                title='岗位一',
                account_id='account-1',
                worker_id='worker-1',
                daily_limit=1,
            )
            second = store.claim(
                company='公司二',
                title='岗位二',
                account_id='account-1',
                worker_id='worker-2',
                daily_limit=1,
            )

            self.assertTrue(first['accepted'])
            self.assertEqual(second['reason'], 'daily_limit')
            self.assertEqual(second['limit'], 1)


class ConfigurationTests(unittest.TestCase):
    def test_llm_greeting_switch_requires_a_boolean(self):
        enabled = copy.deepcopy(config.DEFAULT_USER_CONFIG)
        enabled['llm_greeting_enabled'] = False
        admin_store.validate_config(enabled)

        invalid = copy.deepcopy(config.DEFAULT_USER_CONFIG)
        invalid['llm_greeting_enabled'] = 'false'
        with self.assertRaisesRegex(ValueError, 'llm_greeting_enabled'):
            admin_store.validate_config(invalid)

    def test_example_uses_current_scoring_schema_and_passes_validation(self):
        payload = json.loads((ROOT / 'user_config.example.json').read_text(encoding='utf-8'))
        admin_store.validate_config(payload)
        self.assertEqual(
            set(payload['scoring']),
            {'title_deduction_keywords', 'detail_deduction_keywords'},
        )

    def test_legacy_percentage_rules_migrate_without_unused_positive_groups(self):
        migrated = config._unify_scoring_rules({
            'title_block_keywords': {'销售': 100},
            'title_penalty_keywords': {'Java': 30},
            'title_strong_keywords': {'运维': 90},
            'detail_negative_keywords': {'单休': 40},
            'detail_support_keywords': {'Linux': 80},
        })
        self.assertEqual(migrated['title_deduction_keywords'], {'销售': 5, 'Java': 2})
        self.assertEqual(migrated['detail_deduction_keywords'], {'单休': 2})

    def test_config_validation_covers_all_runtime_fields_and_database_name(self):
        candidate = copy.deepcopy(config.DEFAULT_USER_CONFIG)
        candidate['frontend']['preloadActivateCardWaitMs'] = 600001
        with self.assertRaisesRegex(ValueError, 'preloadActivateCardWaitMs'):
            admin_store.validate_config(candidate)

        candidate = copy.deepcopy(config.DEFAULT_USER_CONFIG)
        candidate['backend']['delivery_db_path'] = ''
        with self.assertRaisesRegex(ValueError, '非空文件名'):
            admin_store.validate_config(candidate)


if __name__ == '__main__':
    unittest.main()
