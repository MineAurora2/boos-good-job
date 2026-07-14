import unittest
from unittest.mock import patch

from runtime_monitor import RuntimeMonitor


class RuntimeMonitorTest(unittest.TestCase):
    def test_heartbeat_registers_one_worker_and_publishes_logs(self):
        monitor = RuntimeMonitor(client_ttl_seconds=30)
        result = monitor.heartbeat({
            'workerId': 'worker-one',
            'accountId': '账号一',
            'scriptVersion': 'test',
            'state': 'running',
            'logs': [{'level': 'info', 'message': '开始运行'}],
        })

        self.assertEqual(result['activeClientCount'], 1)
        self.assertEqual(result['connectedClientCount'], 1)
        self.assertEqual(result['clients'][0]['accountId'], '账号一')
        self.assertEqual([event['type'] for event in monitor.recent_events()], ['client_connected', 'script_log'])

    def test_repeated_heartbeat_does_not_duplicate_worker(self):
        monitor = RuntimeMonitor()
        monitor.heartbeat({'workerId': 'same', 'accountId': 'A'})
        monitor.heartbeat({'workerId': 'same', 'accountId': 'B'})

        snapshot = monitor.snapshot()
        self.assertEqual(snapshot['connectedClientCount'], 1)
        self.assertEqual(snapshot['clients'][0]['accountId'], 'B')

    def test_worker_remains_online_119_seconds_after_heartbeat(self):
        monitor = RuntimeMonitor(client_ttl_seconds=120)
        with patch('runtime_monitor.time.monotonic', side_effect=[1000.0, 1000.0, 1119.0]):
            monitor.heartbeat({'workerId': 'worker-one', 'state': 'running'})
            snapshot = monitor.snapshot()

        self.assertTrue(snapshot['clients'][0]['online'])
        self.assertEqual(snapshot['activeClientCount'], 1)

    def test_worker_becomes_offline_after_120_seconds(self):
        monitor = RuntimeMonitor(client_ttl_seconds=120)
        with patch('runtime_monitor.time.monotonic', side_effect=[1000.0, 1000.0, 1120.001]):
            monitor.heartbeat({'workerId': 'worker-one', 'state': 'running'})
            snapshot = monitor.snapshot()

        self.assertFalse(snapshot['clients'][0]['online'])
        self.assertEqual(snapshot['activeClientCount'], 0)

    def test_stopped_worker_is_immediately_offline(self):
        monitor = RuntimeMonitor(client_ttl_seconds=120)
        monitor.heartbeat({'workerId': 'worker-one', 'state': 'stopped'})

        snapshot = monitor.snapshot()
        self.assertFalse(snapshot['clients'][0]['online'])
        self.assertEqual(snapshot['activeClientCount'], 0)

    def test_heartbeat_never_delivers_targeted_remote_commands(self):
        monitor = RuntimeMonitor()
        monitor.enqueue_command('pause', 'worker-one', {'reason': 'manual'})

        heartbeat = monitor.heartbeat({'workerId': 'worker-one', 'accountId': 'A'})
        self.assertEqual(heartbeat['commands'], [])
        self.assertNotIn('control', heartbeat)

    def test_global_remote_command_is_not_delivered_to_workers(self):
        monitor = RuntimeMonitor()
        monitor.enqueue_command('resume_all')
        first = monitor.heartbeat({'workerId': 'one'})
        second = monitor.heartbeat({'workerId': 'two'})

        self.assertEqual(first['commands'], [])
        self.assertEqual(second['commands'], [])
        self.assertNotIn('control', first)

    def test_safety_plan_account_and_errors_are_exposed(self):
        monitor = RuntimeMonitor()
        monitor.update_safety({'scanOnly': True})
        monitor.update_plan({'dailyTarget': 25, 'maxConsecutiveFailures': 4})
        monitor.update_account('account-a', {'alias': '主账号', 'dailyLimit': 30, 'paused': True})
        error = monitor.record_error({'workerId': 'one', 'message': 'network failed'})

        control = monitor.effective_control('one', 'account-a')
        self.assertTrue(control['safety']['scanOnly'])
        self.assertTrue(control['shouldPause'])
        self.assertEqual(control['plan']['dailyTarget'], 25)
        self.assertEqual(monitor.control_state()['unresolvedErrorCount'], 1)

        monitor.resolve_error(error['id'])
        self.assertEqual(monitor.control_state()['unresolvedErrorCount'], 0)

    def test_heartbeat_accepts_structured_events_and_errors(self):
        monitor = RuntimeMonitor()
        monitor.heartbeat({
            'workerId': 'one',
            'events': [{'type': 'job_scored', 'stars': 3}],
            'errors': [{'type': 'detail_timeout', 'message': 'timeout'}],
            'currentDecision': {'stars': 3, 'title': '运维工程师'},
            'queue': [{'title': '待处理岗位'}],
        })

        state = monitor.control_state()
        self.assertEqual(state['clients'][0]['currentDecision']['stars'], 3)
        self.assertEqual(state['clients'][0]['queue'][0]['title'], '待处理岗位')
        self.assertEqual(state['errors'][0]['type'], 'detail_timeout')
        self.assertIn('job_scored', [event['type'] for event in state['events']])


if __name__ == '__main__':
    unittest.main()
