"""Contract tests for the modular FastAPI application composition."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient

from app_state import ApplicationState
import config
import main
import routes.control as control_routes
import routes.delivery as delivery_routes


EXPECTED_ROUTES = {
    ('GET', '/tags'),
    ('GET', '/get-introduce'),
    ('GET', '/client-config'),
    ('GET', '/dashboard'),
    ('GET', '/dashboard/styles.css'),
    ('GET', '/dashboard/app.js'),
    ('GET', '/dashboard/china.json'),
    ('GET', '/dashboard/china-cities.json'),
    ('GET', '/api/dashboard'),
    ('POST', '/api/admin/deliveries/delete'),
    ('POST', '/api/runtime/heartbeat'),
    ('GET', '/api/runtime'),
    ('GET', '/api/control/state'),
    ('PUT', '/api/control/accounts/{account_id}'),
    ('PUT', '/api/control/errors/{error_id}'),
    ('GET', '/api/control/health'),
    ('GET', '/api/control/diagnostics'),
    ('POST', '/api/control/reload-config'),
    ('GET', '/api/runtime/events'),
    ('GET', '/api/admin/config'),
    ('PUT', '/api/admin/config'),
    ('GET', '/api/admin/llm'),
    ('PUT', '/api/admin/llm'),
    ('POST', '/api/admin/llm/test'),
    ('GET', '/api/admin/resumes'),
    ('PUT', '/api/admin/resumes/current'),
    ('GET', '/api/admin/resumes/{name}'),
    ('PUT', '/api/admin/resumes/{name}'),
    ('GET', '/api/admin/prompts'),
    ('PUT', '/api/admin/prompts'),
    ('POST', '/get-job-score'),
    ('POST', '/generate-introduce'),
    ('POST', '/generate-introduce/start'),
    ('GET', '/generate-introduce/status/{job_id}'),
    ('POST', '/log-action'),
    ('POST', '/delivery/claim'),
    ('POST', '/delivery/mark'),
    ('POST', '/delivery/release'),
    ('POST', '/check-greet'),
    ('POST', '/log-greet'),
    ('POST', '/increment-daily-greet'),
    ('POST', '/check-daily-limit'),
}


class ApplicationStructureTests(unittest.TestCase):
    def test_domain_routers_preserve_the_public_api_contract(self):
        app = main.create_app()
        actual = {
            (method, route.path)
            for route in app.routes
            for method in (route.methods or set())
            if route.path not in {'/openapi.json', '/docs', '/docs/oauth2-redirect', '/redoc'}
        }
        self.assertEqual(actual, EXPECTED_ROUTES)

    def test_application_factory_does_not_duplicate_routes(self):
        app = main.create_app()
        signatures = [
            (method, route.path)
            for route in app.routes
            for method in (route.methods or set())
        ]
        self.assertEqual(len(signatures), len(set(signatures)))

    def test_lifespan_and_read_only_endpoints_work_with_isolated_state(self):
        tests_dir = Path(__file__).resolve().parent
        root = tests_dir / f'.app-structure-{os.getpid()}-{uuid.uuid4().hex}'
        root.mkdir()
        self.addCleanup(self._remove_tree, root, tests_dir)
        config_path = root / 'user_config.json'
        config_path.write_text(
            json.dumps(copy.deepcopy(config.DEFAULT_USER_CONFIG), ensure_ascii=False),
            encoding='utf-8',
        )
        state = ApplicationState(root)

        try:
            with patch.object(config, 'CONFIG_PATH', config_path), patch.object(
                main,
                'STATE',
                state,
            ), patch.object(control_routes, 'STATE', state), patch.object(delivery_routes, 'STATE', state):
                with TestClient(main.create_app()) as client:
                    self.assertEqual(client.get('/client-config').status_code, 200)
                    self.assertEqual(client.get('/api/runtime').status_code, 200)
                    self.assertEqual(client.get('/api/dashboard').json()['deliveries'], [])
                    self.assertTrue(client.get('/api/control/health').json()['checks']['database']['ok'])
        finally:
            config.Config.reload()

    @staticmethod
    def _remove_tree(root: Path, expected_parent: Path) -> None:
        if root.parent != expected_parent or not root.name.startswith('.app-structure-'):
            raise AssertionError(f'unsafe test cleanup path: {root}')
        if not root.exists():
            return
        for path in sorted(root.rglob('*'), key=lambda item: len(item.parts), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
        root.rmdir()


class IntroduceTaskLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_task_is_removed_from_claim_index(self):
        started = asyncio.Event()
        release = asyncio.Event()
        job_id = 'job-cancel-test'
        claim_token = 'claim-cancel-test'

        async def generate(_payload):
            started.set()
            await release.wait()

        delivery_routes._INTRODUCE_JOBS.clear()
        delivery_routes._INTRODUCE_JOB_BY_CLAIM.clear()
        with patch.object(delivery_routes, '_generate_introduce_result', side_effect=generate):
            task = asyncio.create_task(
                delivery_routes._run_introduce_job(job_id, {'claimToken': claim_token})
            )
            delivery_routes._INTRODUCE_JOBS[job_id] = {
                'claimToken': claim_token,
                'status': 'pending',
                'result': None,
                'error': '',
                'updatedAt': 0,
                'task': task,
            }
            delivery_routes._INTRODUCE_JOB_BY_CLAIM[claim_token] = job_id
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertNotIn(job_id, delivery_routes._INTRODUCE_JOBS)
        self.assertNotIn(claim_token, delivery_routes._INTRODUCE_JOB_BY_CLAIM)


if __name__ == '__main__':
    unittest.main()
