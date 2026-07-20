from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from app.runtime import RuntimeMonitor


class RuntimeLogProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.monitor = RuntimeMonitor()

    @staticmethod
    def event_of_type(monitor: RuntimeMonitor, event_type: str) -> dict:
        return next(event for event in monitor.recent_events(100) if event['type'] == event_type)

    def heartbeat(self, payload: dict) -> dict:
        return self.monitor.heartbeat({
            'protocolVersion': 2,
            'scriptApiVersion': 2,
            'sessionId': 'session-1',
            'sessionEpoch': 1,
            'sequence': 1,
            'executionState': 'stopped',
            **payload,
        })

    def test_legacy_script_log_gets_compatible_defaults(self) -> None:
        self.heartbeat({
            'workerId': 'worker-1',
            'accountId': 'account-1',
            'logs': [{'message': 'legacy log'}],
        })

        event = self.event_of_type(self.monitor, 'script_log')
        self.assertEqual(event['sender'], 'system')
        self.assertEqual(event['verbosity'], 'normal')
        self.assertEqual(event['level'], 'info')
        self.assertEqual(event['payload']['sender'], 'system')
        self.assertEqual(event['payload']['verbosity'], 'normal')
        self.assertEqual(event['payload']['level'], 'info')

    def test_script_log_accepts_source_alias_and_normalizes_enums(self) -> None:
        self.heartbeat({
            'workerId': 'worker-1',
            'logs': [{
                'source': ' CLAIM ',
                'verbosity': 'DETAILED',
                'level': 'WARNING',
                'message': 'claim detail',
            }],
        })

        event = self.event_of_type(self.monitor, 'script_log')
        self.assertEqual(
            (event['sender'], event['verbosity'], event['level']),
            ('claim', 'detailed', 'warning'),
        )
        self.assertEqual(event['payload']['sender'], 'claim')
        self.assertNotIn('source', event['payload'])

    def test_invalid_script_log_enums_fall_back_without_rejecting_batch(self) -> None:
        self.heartbeat({
            'workerId': 'worker-1',
            'logs': [{
                'sender': 'operator',
                'verbosity': 'verbose',
                'level': 'trace',
                'message': 'invalid metadata',
            }],
        })

        event = self.event_of_type(self.monitor, 'script_log')
        self.assertEqual(
            (event['sender'], event['verbosity'], event['level']),
            ('system', 'normal', 'info'),
        )
        self.assertEqual(
            (event['payload']['sender'], event['payload']['verbosity'], event['payload']['level']),
            ('system', 'normal', 'info'),
        )

    def test_publish_adds_normalized_metadata_to_every_event(self) -> None:
        event = self.monitor.publish('custom_event', {
            'source': 'delivery',
            'verbosity': 'concise',
            'level': 'warning',
        })

        self.assertEqual(
            (event['sender'], event['verbosity'], event['level']),
            ('delivery', 'concise', 'warning'),
        )
        default_event = self.monitor.publish('plain_event', {})
        self.assertEqual(
            (default_event['sender'], default_event['verbosity'], default_event['level']),
            ('system', 'normal', 'action'),
        )

    def test_error_script_log_publishes_log_and_runtime_error_with_same_metadata(self) -> None:
        self.heartbeat({
            'workerId': 'worker-1',
            'accountId': 'account-1',
            'logs': [{
                'sender': 'delivery',
                'verbosity': 'concise',
                'level': 'error',
                'message': 'send failed',
            }],
        })

        events = self.monitor.recent_events(100)
        script_event = next(event for event in events if event['type'] == 'script_log')
        error_event = next(event for event in events if event['type'] == 'runtime_error')
        self.assertEqual(
            (script_event['sender'], script_event['verbosity'], script_event['level']),
            ('delivery', 'concise', 'error'),
        )
        self.assertEqual(
            (error_event['sender'], error_event['verbosity'], error_event['level']),
            ('delivery', 'concise', 'error'),
        )
        self.assertEqual(error_event['payload']['sender'], 'delivery')

    def test_legacy_action_sender_mapping(self) -> None:
        cases = {
            'delivery_claim_started': 'claim',
            'greet_queued': 'queue',
            'delivery_waiting': 'queue',
            'chat_open_requested': 'queue',
            'greet_sent': 'delivery',
            'resume_sent': 'delivery',
            'job_below_threshold': 'delivery',
            'config_reloaded': 'system',
        }

        for action_name, expected_sender in cases.items():
            with self.subTest(action=action_name):
                monitor = RuntimeMonitor()
                monitor.record_action({'action': action_name})
                event = self.event_of_type(monitor, 'job_action')
                self.assertEqual(event['sender'], expected_sender)
                self.assertEqual(event['payload']['sender'], expected_sender)
                self.assertEqual(event['payload']['verbosity'], 'normal')

    def test_action_keeps_explicit_metadata_and_source_alias(self) -> None:
        self.monitor.record_action({
            'action': 'custom_action',
            'source': 'queue',
            'verbosity': 'concise',
            'level': 'warning',
        })

        event = self.event_of_type(self.monitor, 'job_action')
        self.assertEqual(
            (event['sender'], event['verbosity'], event['level']),
            ('queue', 'concise', 'warning'),
        )
        self.assertEqual(event['payload']['sender'], 'queue')
        self.assertNotIn('source', event['payload'])

    def test_invalid_action_metadata_falls_back_to_inferred_sender(self) -> None:
        self.monitor.record_action({
            'action': 'greet_sent',
            'sender': 'browser',
            'verbosity': 'verbose',
            'level': 'trace',
        })

        event = self.event_of_type(self.monitor, 'job_action')
        self.assertEqual(
            (event['sender'], event['verbosity'], event['level']),
            ('delivery', 'normal', 'action'),
        )


class RuntimeControlProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_path = Path(__file__).parent / f'.test_runtime_control_{uuid.uuid4().hex}.json'
        self.monitor = RuntimeMonitor(client_ttl_seconds=30, state_path=self.state_path)

    def tearDown(self) -> None:
        for path in self.state_path.parent.glob(f'.{self.state_path.name}.*.tmp'):
            path.unlink(missing_ok=True)
        self.state_path.unlink(missing_ok=True)

    @staticmethod
    def heartbeat_payload(
        worker_id: str,
        *,
        session_id: str = 'session-a',
        session_epoch: int = 100,
        sequence: int = 1,
        execution_state: str = 'stopped',
        state: str = 'online',
        control_ack: dict | None = None,
        control_handoff: dict | None = None,
    ) -> dict:
        payload = {
            'workerId': worker_id,
            'accountId': f'account-{worker_id}',
            'alias': f'alias-{worker_id}',
            'protocolVersion': 2,
            'scriptApiVersion': 2,
            'sessionId': session_id,
            'sessionEpoch': session_epoch,
            'sequence': sequence,
            'executionState': execution_state,
            'state': state,
        }
        if control_ack is not None:
            payload['controlAck'] = control_ack
        if control_handoff is not None:
            payload['controlHandoff'] = control_handoff
        return payload

    def test_first_registration_defaults_to_stopped_and_stays_connected(self) -> None:
        response = self.monitor.heartbeat(self.heartbeat_payload('worker-a'))

        self.assertEqual(response['control']['desiredState'], 'stopped')
        self.assertEqual(response['control']['revision'], 0)
        self.assertIsNone(response['control']['operationId'])
        self.assertEqual(response['registeredWorkerCount'], 1)
        self.assertEqual(response['connectedClientCount'], 1)
        self.assertEqual(response['runningClientCount'], 0)
        self.assertTrue(response['clients'][0]['online'])
        self.assertEqual(response['clients'][0]['executionState'], 'stopped')

    def test_expired_worker_is_unregistered_and_reregisters_stopped(self) -> None:
        self.monitor.heartbeat(self.heartbeat_payload('worker-a'))
        self.monitor.set_worker_desired_state('worker-a', 'running')
        with self.monitor._condition:
            self.monitor._clients['worker-a']['_seenMonotonic'] -= 31

        expired = self.monitor.snapshot()

        self.assertEqual(expired['clients'], [])
        self.assertEqual(expired['registeredWorkerCount'], 0)
        self.assertEqual(expired['connectedClientCount'], 0)
        self.assertNotIn('worker-a', self.monitor._clients)
        stored = json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertNotIn('worker-a', stored['workers'])

        reconnected = self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', sequence=2, execution_state='stopped',
        ))
        self.assertEqual(reconnected['control']['desiredState'], 'stopped')
        self.assertEqual(reconnected['registeredWorkerCount'], 1)

    def test_closed_worker_is_unregistered_immediately(self) -> None:
        response = self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', state='closed',
        ))

        self.assertEqual(response['clients'], [])
        self.assertEqual(response['registeredWorkerCount'], 0)
        self.assertEqual(response['connectedClientCount'], 0)
        self.assertNotIn('worker-a', self.monitor._clients)
        stored = json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertNotIn('worker-a', stored['workers'])

    def test_new_session_resets_persisted_running_control_to_stopped(self) -> None:
        initial = self.monitor.heartbeat(self.heartbeat_payload('worker-a'))
        running = self.monitor.set_worker_desired_state('worker-a', 'running')
        running_ack = {
            'epoch': initial['control']['epoch'],
            'revision': running['revision'],
            'operationId': running['operationId'],
            'status': 'applied',
            'executionState': 'running',
        }
        self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', sequence=2, execution_state='running', control_ack=running_ack,
        ))

        response = self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', session_id='session-b', session_epoch=101,
            execution_state='stopped',
        ))

        control = response['control']
        self.assertEqual(control['desiredState'], 'stopped')
        self.assertGreater(control['revision'], running['revision'])
        self.assertTrue(control['operationId'])
        self.assertNotEqual(control['operationId'], running['operationId'])
        worker = json.loads(self.state_path.read_text(encoding='utf-8'))['workers']['worker-a']
        self.assertEqual(worker['desiredState'], 'stopped')
        self.assertEqual(worker['revision'], control['revision'])
        self.assertEqual(worker['operationId'], control['operationId'])
        self.assertEqual(worker['sessionId'], 'session-b')
        self.assertEqual(worker['sessionEpoch'], 101)
        self.assertIsNone(worker['controlAck'])

    def test_navigation_session_handoff_preserves_running_control(self) -> None:
        initial = self.monitor.heartbeat(self.heartbeat_payload('worker-a'))
        running = self.monitor.set_worker_desired_state('worker-a', 'running')
        running_ack = {
            'epoch': initial['control']['epoch'],
            'revision': running['revision'],
            'operationId': running['operationId'],
            'status': 'applied',
            'executionState': 'running',
        }
        self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', sequence=2, execution_state='running', control_ack=running_ack,
        ))

        response = self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a',
            session_id='session-b',
            session_epoch=101,
            execution_state='stopped',
            control_handoff={
                'desiredState': 'running',
                'controlEpoch': initial['control']['epoch'],
                'revision': running['revision'],
                'operationId': running['operationId'],
                'sessionId': 'session-a',
                'sessionEpoch': 100,
            },
        ))

        control = response['control']
        self.assertEqual(control['desiredState'], 'running')
        self.assertEqual(control['revision'], running['revision'])
        self.assertEqual(control['operationId'], running['operationId'])
        worker = json.loads(self.state_path.read_text(encoding='utf-8'))['workers']['worker-a']
        self.assertEqual(worker['desiredState'], 'running')
        self.assertIsNone(worker['controlAck'])
        self.assertEqual(worker['sessionId'], 'session-b')

    def test_same_session_heartbeat_preserves_manually_selected_control(self) -> None:
        self.monitor.heartbeat(self.heartbeat_payload('worker-a'))
        running = self.monitor.set_worker_desired_state('worker-a', 'running')

        response = self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', sequence=2, execution_state='running',
        ))

        self.assertEqual(response['control']['desiredState'], 'running')
        self.assertEqual(response['control']['revision'], running['revision'])
        self.assertEqual(response['control']['operationId'], running['operationId'])

    def test_global_update_targets_registered_workers_but_not_future_workers(self) -> None:
        self.monitor.heartbeat(self.heartbeat_payload('worker-a'))
        first = self.monitor.set_global_desired_state('running')
        repeated = self.monitor.set_global_desired_state('running')

        self.assertEqual(first['targetCount'], 1)
        self.assertGreater(repeated['revision'], first['revision'])
        self.assertNotEqual(repeated['operationId'], first['operationId'])
        self.monitor.heartbeat(self.heartbeat_payload('worker-b'))
        clients = {item['workerId']: item for item in self.monitor.snapshot()['clients']}
        self.assertEqual(clients['worker-a']['desiredState'], 'running')
        self.assertEqual(clients['worker-a']['revision'], repeated['revision'])
        self.assertEqual(clients['worker-b']['desiredState'], 'stopped')
        self.assertEqual(clients['worker-b']['revision'], 0)

    def test_single_worker_update_is_isolated_and_unknown_worker_fails(self) -> None:
        self.monitor.heartbeat(self.heartbeat_payload('worker-a'))
        self.monitor.heartbeat(self.heartbeat_payload('worker-b'))

        result = self.monitor.set_worker_desired_state('worker-a', 'paused')
        clients = {item['workerId']: item for item in self.monitor.snapshot()['clients']}
        self.assertEqual(result['targetCount'], 1)
        self.assertEqual(clients['worker-a']['desiredState'], 'paused')
        self.assertEqual(clients['worker-b']['desiredState'], 'stopped')
        with self.assertRaisesRegex(KeyError, 'worker_not_found'):
            self.monitor.set_worker_desired_state('missing', 'running')
        with self.assertRaisesRegex(ValueError, 'invalid_desired_state'):
            self.monitor.set_global_desired_state('restart')

    def test_desired_control_poll_does_not_persist_and_returns_detached_control(self) -> None:
        self.monitor.heartbeat(self.heartbeat_payload('worker-a'))
        operation = self.monitor.set_worker_desired_state('worker-a', 'paused')

        with patch.object(
            self.monitor,
            '_persist_state_locked',
            side_effect=AssertionError('poll must not persist state'),
        ):
            control = self.monitor.desired_control(
                'worker-a',
                protocol_version=2,
                session_id='session-a',
                session_epoch=100,
            )

        self.assertEqual(control['revision'], operation['revision'])
        self.assertEqual(control['operationId'], operation['operationId'])
        self.assertEqual(control['desiredState'], 'paused')
        control['desiredState'] = 'running'
        stored = json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertEqual(stored['workers']['worker-a']['desiredState'], 'paused')
        restored = RuntimeMonitor(client_ttl_seconds=30, state_path=self.state_path)
        with self.assertRaisesRegex(KeyError, 'worker_not_found'):
            restored.desired_control(
                'worker-a',
                protocol_version=2,
                session_id='session-a',
                session_epoch=100,
            )
        self.assertEqual(restored.snapshot()['connectedClientCount'], 0)
        self.assertEqual(restored._clients, {})
        restored_state = json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertNotIn('worker-a', restored_state['workers'])
        with self.assertRaisesRegex(KeyError, 'worker_not_found'):
            self.monitor.desired_control(
                'missing',
                protocol_version=2,
                session_id='session-a',
                session_epoch=100,
            )
        with self.monitor._condition:
            seen_before = self.monitor._clients['worker-a']['_seenMonotonic']
            last_seen_before = self.monitor._clients['worker-a']['lastSeen']
        with self.assertRaisesRegex(ValueError, 'stale_session'):
            self.monitor.desired_control(
                'worker-a',
                protocol_version=2,
                session_id='old-session',
                session_epoch=100,
            )
        with self.monitor._condition:
            self.assertEqual(self.monitor._clients['worker-a']['_seenMonotonic'], seen_before)
            self.assertEqual(self.monitor._clients['worker-a']['lastSeen'], last_seen_before)

    def test_desired_control_poll_refreshes_client_liveness(self) -> None:
        self.monitor.heartbeat(self.heartbeat_payload('worker-a'))
        with self.monitor._condition:
            self.monitor._clients['worker-a']['_seenMonotonic'] -= 29
            seen_before = self.monitor._clients['worker-a']['_seenMonotonic']
            last_seen_before = self.monitor._clients['worker-a']['lastSeen']

        with patch.object(self.monitor, '_now_iso', return_value='9999-12-31T23:59:59'):
            self.monitor.desired_control(
                'worker-a',
                protocol_version=2,
                session_id='session-a',
                session_epoch=100,
            )

        client = self.monitor.snapshot()['clients'][0]
        self.assertTrue(client['online'])
        with self.monitor._condition:
            self.assertGreater(self.monitor._clients['worker-a']['_seenMonotonic'], seen_before)
        self.assertGreater(client['lastSeen'], last_seen_before)

    def test_desired_control_long_poll_wakes_on_new_revision(self) -> None:
        initial = self.monitor.heartbeat(self.heartbeat_payload('worker-a'))['control']
        wait_started = threading.Event()
        revision_triggered = threading.Event()
        condition_wait = self.monitor._condition.wait
        poll_thread = None

        def mark_wait_started(timeout: float | None = None) -> bool:
            wait_started.set()
            return condition_wait(timeout)

        def poll_control() -> dict:
            nonlocal poll_thread
            poll_thread = threading.current_thread()
            return self.monitor.desired_control(
                'worker-a',
                protocol_version=2,
                session_id='session-a',
                session_epoch=100,
                after_epoch=initial['epoch'],
                after_revision=initial['revision'],
                timeout_seconds=1,
            )

        def controlled_now() -> str:
            if threading.current_thread() is not poll_thread:
                return RuntimeMonitor._now_iso()
            return (
                '2099-01-01T00:00:02'
                if revision_triggered.is_set()
                else '2099-01-01T00:00:01'
            )

        with patch.object(self.monitor, '_now_iso', side_effect=controlled_now):
            with patch.object(self.monitor._condition, 'wait', side_effect=mark_wait_started):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending = executor.submit(poll_control)
                    self.assertTrue(wait_started.wait(1), 'desired_control did not enter wait')
                    with self.monitor._condition:
                        first_poll_seen = self.monitor._clients['worker-a']['_seenMonotonic']
                        first_poll_last_seen = self.monitor._clients['worker-a']['lastSeen']
                        self.monitor._clients['worker-a']['_seenMonotonic'] -= 1
                        aged_poll_seen = self.monitor._clients['worker-a']['_seenMonotonic']
                    command_started_at = time.monotonic()
                    revision_triggered.set()
                    operation = self.monitor.set_worker_desired_state('worker-a', 'running')
                    control = pending.result(timeout=1)
                    delivery_latency = time.monotonic() - command_started_at

        with self.monitor._condition:
            second_poll_seen = self.monitor._clients['worker-a']['_seenMonotonic']
            second_poll_last_seen = self.monitor._clients['worker-a']['lastSeen']
        public_client = self.monitor.snapshot()['clients'][0]

        self.assertEqual(control['revision'], operation['revision'])
        self.assertEqual(control['operationId'], operation['operationId'])
        self.assertEqual(control['desiredState'], 'running')
        self.assertGreater(second_poll_seen, aged_poll_seen)
        self.assertGreaterEqual(second_poll_seen, first_poll_seen)
        self.assertEqual(first_poll_last_seen, '2099-01-01T00:00:01')
        self.assertEqual(second_poll_last_seen, '2099-01-01T00:00:02')
        self.assertNotEqual(second_poll_last_seen, first_poll_last_seen)
        self.assertEqual(public_client['lastSeen'], second_poll_last_seen)
        self.assertLess(delivery_latency, 1.0)

    def test_desired_control_long_poll_revalidates_session_after_wakeup(self) -> None:
        initial = self.monitor.heartbeat(self.heartbeat_payload('worker-a'))['control']
        wait_started = threading.Event()
        condition_wait = self.monitor._condition.wait

        def mark_wait_started(timeout: float | None = None) -> bool:
            wait_started.set()
            return condition_wait(timeout)

        with patch.object(self.monitor, '_now_iso', return_value='2099-01-01T00:00:01'):
            with patch.object(self.monitor._condition, 'wait', side_effect=mark_wait_started):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending = executor.submit(
                        self.monitor.desired_control,
                        'worker-a',
                        protocol_version=2,
                        session_id='session-a',
                        session_epoch=100,
                        after_epoch=initial['epoch'],
                        after_revision=initial['revision'],
                        timeout_seconds=1,
                    )
                    self.assertTrue(wait_started.wait(1), 'desired_control did not enter wait')
                    with self.monitor._condition:
                        with patch.object(
                            self.monitor, '_now_iso', return_value='2099-01-01T00:00:02',
                        ):
                            self.monitor.heartbeat(self.heartbeat_payload(
                                'worker-a', session_id='session-b', session_epoch=101,
                            ))
                        replacement_seen = self.monitor._clients['worker-a']['_seenMonotonic']
                        replacement_last_seen = self.monitor._clients['worker-a']['lastSeen']
                    with self.assertRaisesRegex(ValueError, 'stale_session'):
                        pending.result(timeout=1)

        with self.monitor._condition:
            self.assertEqual(
                self.monitor._clients['worker-a']['_seenMonotonic'], replacement_seen,
            )
            self.assertEqual(
                self.monitor._clients['worker-a']['lastSeen'], replacement_last_seen,
            )

    def test_stale_session_and_sequence_cannot_overwrite_live_state(self) -> None:
        self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', session_id='new', session_epoch=200, sequence=5,
            execution_state='running',
        ))

        old_session = self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', session_id='old', session_epoch=100, sequence=99,
            execution_state='error',
        ))
        duplicate = self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', session_id='new', session_epoch=200, sequence=5,
            execution_state='paused',
        ))

        self.assertFalse(old_session['heartbeatAccepted'])
        self.assertEqual(old_session['heartbeatReason'], 'stale_session')
        self.assertFalse(duplicate['heartbeatAccepted'])
        self.assertEqual(duplicate['heartbeatReason'], 'stale_sequence')
        client = self.monitor.snapshot()['clients'][0]
        self.assertEqual(client['executionState'], 'running')
        self.assertEqual(client['sessionId'], 'new')
        self.assertEqual(client['sequence'], 5)

    def test_only_matching_control_ack_updates_sync_state(self) -> None:
        initial = self.monitor.heartbeat(self.heartbeat_payload('worker-a'))
        operation = self.monitor.set_worker_desired_state('worker-a', 'running')
        wrong_ack = {
            'epoch': initial['control']['epoch'],
            'revision': operation['revision'] - 1,
            'operationId': operation['operationId'],
            'status': 'applied',
            'executionState': 'running',
        }
        self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', sequence=2, execution_state='running', control_ack=wrong_ack,
        ))
        client = self.monitor.snapshot()['clients'][0]
        self.assertIsNone(client['controlAck'])
        self.assertEqual(client['syncState'], 'pending')

        matching_ack = {
            **wrong_ack,
            'revision': operation['revision'],
            'status': 'applying',
        }
        self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', sequence=3, execution_state='starting', control_ack=matching_ack,
        ))
        self.assertEqual(self.monitor.snapshot()['clients'][0]['syncState'], 'applying')
        matching_ack['status'] = 'applied'
        matching_ack['executionState'] = 'running'
        self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', sequence=4, execution_state='running', control_ack=matching_ack,
        ))
        self.assertEqual(self.monitor.snapshot()['clients'][0]['syncState'], 'synced')

        retry = self.monitor.set_worker_desired_state('worker-a', 'running')
        failed_ack = {
            'epoch': initial['control']['epoch'],
            'revision': retry['revision'],
            'operationId': retry['operationId'],
            'status': 'failed',
            'executionState': 'error',
            'message': 'executor failed',
        }
        self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', sequence=5, execution_state='error', control_ack=failed_ack,
        ))
        self.assertEqual(self.monitor.snapshot()['clients'][0]['syncState'], 'failed')
        self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', sequence=6, execution_state='running', control_ack=matching_ack,
        ))
        client = self.monitor.snapshot()['clients'][0]
        self.assertEqual(client['revision'], retry['revision'])
        self.assertEqual(client['controlAck']['revision'], retry['revision'])
        self.assertEqual(client['syncState'], 'failed')

    def test_backend_restart_unregisters_worker_without_live_connection(self) -> None:
        first = self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', session_id='new', session_epoch=200, sequence=5,
        ))
        operation = self.monitor.set_worker_desired_state('worker-a', 'paused')
        ack = {
            'epoch': first['control']['epoch'],
            'revision': operation['revision'],
            'operationId': operation['operationId'],
            'status': 'applied',
            'executionState': 'paused',
            'acknowledgedAt': '2026-07-17T12:00:00',
        }
        self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', session_id='new', session_epoch=200, sequence=6,
            execution_state='paused', control_ack=ack,
        ))
        stored = json.loads(self.state_path.read_text(encoding='utf-8'))

        restored = RuntimeMonitor(client_ttl_seconds=30, state_path=self.state_path)
        state = restored.snapshot()
        self.assertEqual(state['controlEpoch'], stored['controlEpoch'])
        self.assertEqual(state['revision'], operation['revision'])
        self.assertEqual(state['registeredWorkerCount'], 0)
        self.assertEqual(state['connectedClientCount'], 0)
        self.assertEqual(state['clients'], [])
        restored_state = json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertNotIn('worker-a', restored_state['workers'])

        reconnected = restored.heartbeat(self.heartbeat_payload(
            'worker-a', session_id='old', session_epoch=100, sequence=100,
            execution_state='running',
        ))
        self.assertTrue(reconnected['heartbeatAccepted'])
        self.assertEqual(reconnected['control']['desiredState'], 'stopped')

    def test_registered_count_excludes_expired_workers(self) -> None:
        self.monitor.heartbeat(self.heartbeat_payload(
            'worker-a', execution_state='running',
        ))
        self.monitor.heartbeat(self.heartbeat_payload(
            'worker-b', execution_state='stopped',
        ))
        with self.monitor._condition:
            self.monitor._clients['worker-a']['_seenMonotonic'] -= 31

        state = self.monitor.snapshot()
        self.assertEqual(state['registeredWorkerCount'], 1)
        self.assertEqual(state['connectedClientCount'], 1)
        self.assertEqual(state['runningClientCount'], 0)
        clients = {item['workerId']: item for item in state['clients']}
        self.assertNotIn('worker-a', clients)
        self.assertTrue(clients['worker-b']['online'])

    def test_account_daily_limit_accepts_freeze_and_rejects_values_above_150(self) -> None:
        frozen = self.monitor.update_account('account-a', {'dailyLimit': 0})
        capped = self.monitor.update_account('account-a', {'dailyLimit': 150})

        self.assertEqual(frozen['dailyLimit'], 0)
        self.assertEqual(capped['dailyLimit'], 150)
        for invalid in (-1, 151, 1.5, True, None):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, 'daily_limit_out_of_range'):
                    self.monitor.update_account('account-a', {'dailyLimit': invalid})
        self.assertEqual(
            self.monitor.effective_control('', 'account-a')['account']['dailyLimit'],
            150,
        )

    def test_persisted_account_daily_limit_is_clamped_during_migration(self) -> None:
        self.monitor.update_account('account-high', {'dailyLimit': 150})
        stored = json.loads(self.state_path.read_text(encoding='utf-8'))
        stored['accounts']['account-high']['dailyLimit'] = 500
        stored['accounts']['account-low'] = {'accountId': 'account-low', 'dailyLimit': -20}
        self.state_path.write_text(json.dumps(stored), encoding='utf-8')

        restored = RuntimeMonitor(state_path=self.state_path)

        self.assertEqual(restored.effective_control('', 'account-high')['account']['dailyLimit'], 150)
        self.assertEqual(restored.effective_control('', 'account-low')['account']['dailyLimit'], 0)


class RuntimeScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_path = Path(__file__).parent / f'.test_runtime_schedule_{uuid.uuid4().hex}.json'
        self.monitor = RuntimeMonitor(client_ttl_seconds=30, state_path=self.state_path)

    def tearDown(self) -> None:
        self.monitor.stop_scheduler()
        for path in self.state_path.parent.glob(f'.{self.state_path.name}.*.tmp'):
            path.unlink(missing_ok=True)
        self.state_path.unlink(missing_ok=True)

    @staticmethod
    def heartbeat(worker_id: str, sequence: int = 1) -> dict:
        return {
            'workerId': worker_id,
            'accountId': f'account-{worker_id}',
            'protocolVersion': 2,
            'scriptApiVersion': 2,
            'sessionId': f'session-{worker_id}',
            'sessionEpoch': 100,
            'sequence': sequence,
            'executionState': 'stopped',
            'state': 'online',
        }

    @staticmethod
    def schedule(enabled: bool = True) -> dict:
        return {
            'enabled': enabled,
            'mode': 'daily',
            'startTime': '09:30',
            'durationMinutes': 120,
            'weekdays': [],
            'dateStart': '',
            'dateEnd': '',
        }

    def desired_states(self) -> dict[str, str]:
        return {
            item['workerId']: item['desiredState']
            for item in self.monitor.snapshot()['clients']
        }

    def test_schedule_plan_persists_and_exposes_waiting_status(self) -> None:
        plan = self.monitor.update_plan({'schedule': self.schedule()})
        status = self.monitor.schedule_status(datetime(2026, 7, 20, 8, 0))
        restored = RuntimeMonitor(state_path=self.state_path)

        self.assertEqual(plan['schedule']['startTime'], '09:30')
        self.assertFalse(status['active'])
        self.assertEqual(status['nextStart'], '2026-07-20T09:30:00')
        self.assertEqual(restored.control_state()['plan']['schedule'], plan['schedule'])

    def test_active_tick_starts_workers_is_idempotent_and_adopts_late_worker(self) -> None:
        self.monitor.heartbeat(self.heartbeat('worker-a'))
        self.monitor.update_plan({'schedule': self.schedule()})
        now = datetime(2026, 7, 20, 10, 0)

        first = self.monitor.run_schedule_tick(now)
        first_revision = self.monitor.snapshot()['revision']
        repeated = self.monitor.run_schedule_tick(now)
        self.monitor.heartbeat(self.heartbeat('worker-b'))
        late = self.monitor.run_schedule_tick(now)

        self.assertTrue(first['active'])
        self.assertEqual(repeated['ownedCount'], 1)
        self.assertEqual(first_revision, repeated['revision'])
        self.assertEqual(self.desired_states(), {'worker-a': 'running', 'worker-b': 'running'})
        self.assertEqual(late['ownedCount'], 2)

    def test_manual_pause_is_suppressed_until_the_next_window(self) -> None:
        self.monitor.heartbeat(self.heartbeat('worker-a'))
        self.monitor.update_plan({'schedule': self.schedule()})
        first_window = datetime(2026, 7, 20, 10, 0)
        self.monitor.run_schedule_tick(first_window)

        self.monitor.set_worker_desired_state('worker-a', 'paused')
        suppressed = self.monitor.run_schedule_tick(first_window)
        next_window = self.monitor.run_schedule_tick(datetime(2026, 7, 21, 10, 0))

        self.assertEqual(suppressed['suppressedCount'], 1)
        self.assertEqual(next_window['suppressedCount'], 0)
        self.assertEqual(self.desired_states()['worker-a'], 'running')

    def test_window_end_pauses_only_workers_owned_by_the_schedule(self) -> None:
        self.monitor.heartbeat(self.heartbeat('worker-a'))
        self.monitor.update_plan({'schedule': self.schedule()})
        self.monitor.run_schedule_tick(datetime(2026, 7, 20, 10, 0))
        self.monitor.run_schedule_tick(datetime(2026, 7, 20, 12, 0))
        self.monitor.heartbeat(self.heartbeat('worker-b'))
        self.monitor.set_worker_desired_state('worker-b', 'running')

        status = self.monitor.run_schedule_tick(datetime(2026, 7, 20, 12, 0, 1))

        self.assertFalse(status['active'])
        self.assertEqual(self.desired_states(), {'worker-a': 'paused', 'worker-b': 'running'})

    def test_disabling_schedule_clears_runtime_ownership_without_pausing(self) -> None:
        self.monitor.heartbeat(self.heartbeat('worker-a'))
        self.monitor.update_plan({'schedule': self.schedule()})
        self.monitor.run_schedule_tick(datetime(2026, 7, 20, 10, 0))

        self.monitor.update_plan({'schedule': self.schedule(False)})
        status = self.monitor.run_schedule_tick(datetime(2026, 7, 20, 10, 1))

        self.assertFalse(status['enabled'])
        self.assertEqual(status['ownedCount'], 0)
        self.assertEqual(self.desired_states()['worker-a'], 'running')

    def test_scheduler_thread_start_and_stop_are_idempotent(self) -> None:
        first = self.monitor.start_scheduler()
        repeated = self.monitor.start_scheduler()

        self.assertTrue(first)
        self.assertFalse(repeated)
        self.assertTrue(self.monitor.stop_scheduler())
        self.assertFalse(self.monitor.stop_scheduler())


if __name__ == '__main__':
    unittest.main()
