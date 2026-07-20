from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from app.protocol import CONTROL_PROTOCOL_VERSION
from app.routes import control as control_routes
from app.runtime import RuntimeMonitor


class RuntimeV2PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_path = Path(__file__).parent / f'.test_runtime_v2_{uuid.uuid4().hex}.json'
        self.monitor = RuntimeMonitor(state_path=self.state_path)

    def tearDown(self) -> None:
        self.monitor.stop_scheduler()
        for path in self.state_path.parent.glob(f'.{self.state_path.name}.*.tmp'):
            path.unlink(missing_ok=True)
        self.state_path.unlink(missing_ok=True)

    @staticmethod
    def heartbeat(version: int = 2) -> dict:
        return {
            'workerId': 'worker-1',
            'accountId': 'account-1',
            'protocolVersion': version,
            'scriptApiVersion': 2,
            'sessionId': 'session-1',
            'sessionEpoch': 1,
            'sequence': 1,
            'executionState': 'stopped',
            'state': 'online',
        }

    def test_v1_heartbeat_is_rejected_even_for_first_registration(self) -> None:
        self.assertEqual(CONTROL_PROTOCOL_VERSION, 2)
        with self.assertRaisesRegex(ValueError, 'unsupported_protocol_version'):
            self.monitor.heartbeat(self.heartbeat(1))
        self.assertEqual(self.monitor.snapshot()['registeredWorkerCount'], 0)

    def test_heartbeat_rejects_old_script_api_and_exposes_v2_in_client_and_control(self) -> None:
        old = self.heartbeat()
        old['scriptApiVersion'] = 1
        with self.assertRaisesRegex(ValueError, 'unsupported_script_api_version'):
            self.monitor.heartbeat(old)

        response = self.monitor.heartbeat(self.heartbeat())
        self.assertEqual(response['clients'][0]['scriptApiVersion'], 2)
        self.assertEqual(response['control']['scriptApiVersion'], 2)

    def test_heartbeat_consecutive_failures_flow_into_effective_policy(self) -> None:
        heartbeat = self.heartbeat()
        heartbeat['consecutiveFailures'] = 3
        self.monitor.heartbeat(heartbeat)
        control = self.monitor.effective_control('worker-1', 'account-1')
        self.assertEqual(control['consecutiveFailures'], 3)
        self.assertEqual(control['policy']['consecutiveFailures'], 3)

    def test_v2_heartbeat_control_contains_all_server_policy_layers(self) -> None:
        self.monitor.update_safety({
            'scanOnly': True,
            'scanAiEnabled': True,
            'sendingDisabled': True,
        })
        self.monitor.update_plan({
            'dailyTarget': 25,
            'hourlyLimit': 4,
            'minDelayMs': 5000,
            'maxDelayMs': 9000,
        })
        self.monitor.update_account('account-1', {'dailyLimit': 30, 'paused': False})

        response = self.monitor.heartbeat(self.heartbeat())
        control = response['control']
        self.assertTrue(control['safety']['scanOnly'])
        self.assertTrue(control['safety']['scanAiEnabled'])
        self.assertEqual(control['plan']['hourlyLimit'], 4)
        self.assertEqual(control['account']['dailyLimit'], 30)
        self.assertTrue(control['policy']['scanOnly'])
        self.assertEqual(control['policy']['dailyLimit'], 30)
        self.assertEqual(control['policy']['minDelayMs'], 5000)

    def test_effective_control_has_stable_account_and_merged_policy(self) -> None:
        control = self.monitor.effective_control('worker-1', 'account-1')
        self.assertEqual(control['account']['accountId'], 'account-1')
        self.assertIn('scanAiEnabled', control['safety'])
        self.assertIn('policy', control)
        self.assertEqual(control['policy']['accountId'], 'account-1')

    def test_safety_and_full_plan_routes_accept_partial_updates(self) -> None:
        request = SimpleNamespace(
            state=SimpleNamespace(goodjob_authorized=True),
            client=SimpleNamespace(host='127.0.0.1'),
        )
        with patch.object(control_routes, 'RUNTIME_MONITOR', self.monitor):
            safety = control_routes.control_update_safety(request, {
                'scanAiEnabled': True,
                'resumeSendingDisabled': True,
            })
            plan = control_routes.control_update_plan(request, {
                'hourlyLimit': 5,
                'dailyTarget': 35,
                'minDelayMs': 1000,
                'maxDelayMs': 3000,
                'applyNow': False,
            })
        self.assertTrue(safety['safety']['scanAiEnabled'])
        self.assertTrue(safety['safety']['resumeSendingDisabled'])
        self.assertEqual(plan['plan']['hourlyLimit'], 5)
        self.assertEqual(plan['plan']['dailyTarget'], 35)


if __name__ == '__main__':
    unittest.main()
