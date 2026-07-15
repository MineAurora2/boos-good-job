"""Runtime monitor persistence and atomic-update regression tests."""

from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from app.runtime import RuntimeMonitor


TESTS_DIR = Path(__file__).resolve().parent


class RuntimeMonitorTests(unittest.TestCase):
    def test_non_object_state_file_falls_back_to_defaults(self):
        path = TESTS_DIR / f'.runtime-monitor-{os.getpid()}-{uuid.uuid4().hex}.json'
        self.addCleanup(path.unlink, missing_ok=True)
        path.write_text('[]', encoding='utf-8')

        monitor = RuntimeMonitor(state_path=path)

        self.assertFalse(monitor.control_state()['safety']['globalPaused'])

    def test_invalid_safety_patch_does_not_partially_update(self):
        monitor = RuntimeMonitor()

        with self.assertRaisesRegex(ValueError, 'unsupported_safety_field'):
            monitor.update_safety({'globalPaused': True, 'unsupported': True})

        self.assertFalse(monitor.control_state()['safety']['globalPaused'])

    def test_invalid_plan_patch_does_not_partially_update(self):
        monitor = RuntimeMonitor()

        with self.assertRaisesRegex(ValueError, 'max_delay_less_than_min_delay'):
            monitor.update_plan({'minDelayMs': 100, 'maxDelayMs': 50})

        plan = monitor.control_state()['plan']
        self.assertEqual(plan['minDelayMs'], 0)
        self.assertEqual(plan['maxDelayMs'], 0)

    def test_invalid_account_patch_does_not_create_partial_policy(self):
        monitor = RuntimeMonitor()

        with self.assertRaises((TypeError, ValueError)):
            monitor.update_account('account-1', {'alias': '测试账号', 'dailyLimit': 'invalid'})

        self.assertNotIn('account-1', monitor.control_state()['accounts'])

    def test_persistence_failure_does_not_commit_memory_state(self):
        path = TESTS_DIR / f'.runtime-monitor-{os.getpid()}-{uuid.uuid4().hex}.json'
        self.addCleanup(path.unlink, missing_ok=True)
        monitor = RuntimeMonitor(state_path=path)

        with patch('app.runtime.atomic_write_text', side_effect=OSError('disk unavailable')):
            with self.assertRaisesRegex(OSError, 'disk unavailable'):
                monitor.update_safety({'globalPaused': True})
            with self.assertRaisesRegex(OSError, 'disk unavailable'):
                monitor.update_account('account-1', {'alias': '测试账号'})

        state = monitor.control_state()
        self.assertFalse(state['safety']['globalPaused'])
        self.assertNotIn('account-1', state['accounts'])


if __name__ == '__main__':
    unittest.main()
