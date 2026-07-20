from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.control import router as control_router
from app.config import Config
from app.runtime import RuntimeMonitor
from app.security import HybridAuthMiddleware, SecurityPolicy


TOKEN = 'test-shared-token-that-is-at-least-32-characters'


class DesiredStateRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_path = Path(__file__).parent / f'.test_control_routes_{uuid.uuid4().hex}.json'
        self.monitor = RuntimeMonitor(state_path=self.state_path)
        self.poll_params = {
            'protocolVersion': 2,
            'scriptApiVersion': 2,
            'sessionId': 'session-a',
            'sessionEpoch': 100,
        }
        self.monitor.heartbeat({
            'workerId': 'worker-a',
            **self.poll_params,
            'sequence': 1,
            'executionState': 'stopped',
        })
        application = FastAPI()
        application.include_router(control_router)
        self.client = TestClient(application)
        self.runtime_patch = patch('app.routes.control.RUNTIME_MONITOR', self.monitor)
        self.runtime_patch.start()
        self.state = Mock()
        self.state.quota_status.side_effect = lambda account_id: {
            'accountId': account_id,
            'count': 0,
            'limit': self.monitor.effective_control('', account_id)['account'].get('dailyLimit', 90),
        }
        self.state_patch = patch('app.routes.control.STATE', self.state)
        self.state_patch.start()

    def tearDown(self) -> None:
        self.state_patch.stop()
        self.runtime_patch.stop()
        self.client.close()
        for path in self.state_path.parent.glob(f'.{self.state_path.name}.*.tmp'):
            path.unlink(missing_ok=True)
        self.state_path.unlink(missing_ok=True)

    def test_echarts_distribution_is_served_from_dashboard_vendor(self) -> None:
        self.state.dashboard_dir = Path(__file__).parents[1] / 'dashboard'

        response = self.client.get('/dashboard/vendor/echarts.min.js')

        self.assertEqual(response.status_code, 200)
        self.assertIn('application/javascript', response.headers['content-type'])
        self.assertGreater(len(response.content), 1_000_000)

    def test_global_desired_state_returns_accepted_operation(self) -> None:
        response = self.client.put(
            '/api/control/desired-state/global',
            json={'desiredState': 'running'},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['desiredState'], 'running')
        self.assertEqual(response.json()['targetCount'], 1)
        self.assertTrue(response.json()['operationId'])

    def test_schedule_plan_route_normalizes_and_applies_now(self) -> None:
        schedule = {
            'enabled': True,
            'mode': 'weekly',
            'startTime': '09:30',
            'durationMinutes': 120,
            'weekdays': [4, 0, 4],
            'dateStart': '',
            'dateEnd': '',
        }
        with patch.object(self.monitor, 'run_schedule_tick', return_value={'active': True}) as tick:
            response = self.client.put(
                '/api/control/plan',
                json={'schedule': schedule, 'applyNow': True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['plan']['schedule']['weekdays'], [0, 4])
        self.assertEqual(response.json()['scheduleStatus'], {'active': True})
        tick.assert_called_once_with()

    def test_schedule_plan_route_rejects_unknown_fields_and_invalid_duration(self) -> None:
        unknown = self.client.put(
            '/api/control/plan',
            json={'schedule': {'enabled': False, 'unexpected': True}},
        )
        invalid = self.client.put(
            '/api/control/plan',
            json={'schedule': {
                'enabled': True,
                'mode': 'daily',
                'startTime': '09:30',
                'durationMinutes': 1441,
                'weekdays': [],
                'dateStart': '',
                'dateEnd': '',
            }},
        )

        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(unknown.json()['detail'], 'unsupported_schedule_field:unexpected')
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()['detail'], 'invalid_schedule_duration')

    def test_worker_desired_state_validates_state_and_identity(self) -> None:
        accepted = self.client.put(
            '/api/control/desired-state/workers/worker-a',
            json={'desiredState': 'paused'},
        )
        invalid = self.client.put(
            '/api/control/desired-state/workers/worker-a',
            json={'desiredState': 'restart'},
        )
        missing = self.client.put(
            '/api/control/desired-state/workers/missing',
            json={'desiredState': 'stopped'},
        )

        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(accepted.json()['targetCount'], 1)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()['detail'], 'invalid_desired_state')
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()['detail'], 'worker_not_found')

    def test_account_daily_limit_route_enforces_zero_through_150(self) -> None:
        frozen = self.client.put('/api/control/accounts/account-a', json={'dailyLimit': 0})
        capped = self.client.put('/api/control/accounts/account-a', json={'dailyLimit': 150})
        too_high = self.client.put('/api/control/accounts/account-a', json={'dailyLimit': 151})
        fractional = self.client.put('/api/control/accounts/account-a', json={'dailyLimit': 1.5})

        self.assertEqual(frozen.status_code, 200)
        self.assertEqual(frozen.json()['policy']['dailyLimit'], 0)
        self.assertEqual(capped.status_code, 200)
        self.assertEqual(capped.json()['policy']['dailyLimit'], 150)
        self.assertEqual(too_high.status_code, 400)
        self.assertEqual(too_high.json()['detail'], 'daily_limit_out_of_range')
        self.assertEqual(fractional.status_code, 400)
        self.assertEqual(fractional.json()['detail'], 'daily_limit_out_of_range')

    def test_reload_config_clamps_inherited_account_limit_to_150(self) -> None:
        self.state.delivery_store.daily_limit = 90
        with (
            patch.object(Config, 'reload'),
            patch.object(Config, 'backend', {'daily_greet_limit': 500}),
            patch('app.routes.control.LLM_MANAGER.reload'),
            patch('app.routes.control.LLM_MANAGER.reset_circuits'),
        ):
            response = self.client.post('/api/control/reload-config')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.state.delivery_store.daily_limit, 150)

    def test_worker_desired_state_poll_returns_latest_control(self) -> None:
        initial = self.client.get(
            '/api/control/workers/worker-a/desired-state', params=self.poll_params,
        )
        operation = self.monitor.set_worker_desired_state('worker-a', 'running')
        updated = self.client.get(
            '/api/control/workers/worker-a/desired-state', params=self.poll_params,
        )
        missing = self.client.get(
            '/api/control/workers/missing/desired-state', params=self.poll_params,
        )

        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.headers['cache-control'], 'no-store')
        self.assertEqual(initial.json()['control']['desiredState'], 'stopped')
        self.assertEqual(updated.status_code, 200)
        updated_control = updated.json()['control']
        self.assertEqual(updated_control['epoch'], initial.json()['control']['epoch'])
        self.assertEqual(updated_control['revision'], operation['revision'])
        self.assertEqual(updated_control['operationId'], operation['operationId'])
        self.assertEqual(updated_control['desiredState'], 'running')
        self.assertIn('safety', updated_control)
        self.assertIn('plan', updated_control)
        self.assertIn('account', updated_control)
        self.assertIn('policy', updated_control)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()['detail'], 'worker_not_found')

    def test_worker_desired_state_poll_rejects_stale_session_without_writing(self) -> None:
        persisted_before = self.state_path.read_bytes()
        stale = self.client.get(
            '/api/control/workers/worker-a/desired-state',
            params={**self.poll_params, 'sessionId': 'superseded-session'},
        )

        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['detail'], 'stale_session')
        self.assertEqual(self.state_path.read_bytes(), persisted_before)
        self.assertEqual(self.monitor.snapshot()['clients'][0]['sequence'], 1)

    def test_worker_desired_state_poll_uses_global_bearer_auth(self) -> None:
        application = FastAPI()
        application.add_middleware(
            HybridAuthMiddleware,
            policy=SecurityPolicy.from_env({'GOODJOB_SHARED_TOKEN': TOKEN}),
        )
        application.include_router(control_router)

        with TestClient(
            application,
            base_url='https://backend.example',
            client=('203.0.113.10', 50000),
        ) as client:
            missing_token = client.get('/api/control/workers/worker-a/desired-state')
            accepted = client.get(
                '/api/control/workers/worker-a/desired-state',
                params=self.poll_params,
                headers={'Authorization': f'Bearer {TOKEN}'},
            )

        self.assertEqual(missing_token.status_code, 401)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()['control']['desiredState'], 'stopped')


if __name__ == '__main__':
    unittest.main()
