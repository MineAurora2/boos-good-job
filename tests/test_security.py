from __future__ import annotations

from datetime import datetime
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

import main
from app.security import HybridAuthMiddleware, SecurityConfigurationError, SecurityPolicy
from app.state import require_local_admin


TOKEN = 'test-shared-token-that-is-at-least-32-characters'


def _protected_app(environment: dict[str, str]) -> FastAPI:
    application = FastAPI()
    application.add_middleware(
        HybridAuthMiddleware,
        policy=SecurityPolicy.from_env(environment),
    )

    @application.get('/dashboard')
    async def dashboard():
        return {'public': True}

    @application.get('/dashboard/vendor/echarts.min.js')
    async def dashboard_echarts():
        return {'public': True}

    @application.post('/dashboard')
    async def dashboard_write():
        return {'written': True}

    @application.get('/api/private')
    async def private(request: Request):
        access = request.state.goodjob_access
        return {'authMethod': access.auth_method, 'clientHost': access.client_host}

    @application.get('/api/admin-test')
    async def admin_test(request: Request):
        require_local_admin(request)
        return {'ok': True}

    return application


class SecurityConfigurationTests(unittest.TestCase):
    def test_empty_security_environment_is_valid_for_lan_only_use(self) -> None:
        policy = SecurityPolicy.from_env({})
        self.assertIsNone(policy.shared_token)
        self.assertEqual(policy.trusted_proxies, ())

    def test_token_length_is_validated(self) -> None:
        for token in ('short', 'x' * 257):
            with self.subTest(length=len(token)):
                with self.assertRaisesRegex(SecurityConfigurationError, '32 to 256'):
                    SecurityPolicy.from_env({'GOODJOB_SHARED_TOKEN': token})

    def test_trusted_proxies_reject_wildcards_zero_prefixes_and_bad_entries(self) -> None:
        for value in ('*', '0.0.0.0/0', '::/0', 'proxy.example.com', '127.0.0.1,'):
            with self.subTest(value=value):
                with self.assertRaises(SecurityConfigurationError):
                    SecurityPolicy.from_env({'GOODJOB_TRUSTED_PROXIES': value})


class HybridAuthenticationTests(unittest.TestCase):
    def test_explicit_lan_ranges_are_token_exempt(self) -> None:
        application = _protected_app({})
        hosts = ('127.0.0.1', '10.2.3.4', '172.31.255.254', '192.168.8.9', '169.254.2.3', '::1', 'fd00::5', 'fe80::5')
        for host in hosts:
            with self.subTest(host=host):
                with TestClient(application, client=(host, 50000)) as client:
                    response = client.get('/api/private')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['authMethod'], 'local-network')

    def test_non_lan_private_or_reserved_classifications_are_not_exempt(self) -> None:
        application = _protected_app({})
        with TestClient(
            application,
            base_url='https://backend.example',
            client=('100.64.0.1', 50000),
        ) as client:
            response = client.get('/api/private')
        self.assertEqual(response.status_code, 503)

    def test_dashboard_get_is_public_but_writes_and_docs_are_protected(self) -> None:
        application = _protected_app({})
        with TestClient(application, client=('203.0.113.10', 50000)) as client:
            self.assertEqual(client.get('/dashboard').status_code, 200)
            self.assertEqual(client.get('/dashboard/vendor/echarts.min.js').status_code, 200)
            self.assertEqual(client.post('/dashboard').status_code, 426)
            self.assertEqual(client.get('/docs').status_code, 426)

    def test_public_http_is_rejected_before_authentication(self) -> None:
        application = _protected_app({'GOODJOB_SHARED_TOKEN': TOKEN})
        with TestClient(application, client=('203.0.113.10', 50000)) as client:
            response = client.get(
                '/api/private',
                headers={'Authorization': f'Bearer {TOKEN}'},
            )
        self.assertEqual(response.status_code, 426)

    def test_public_https_requires_configured_valid_bearer_token(self) -> None:
        no_token_app = _protected_app({})
        with TestClient(
            no_token_app,
            base_url='https://backend.example',
            client=('203.0.113.10', 50000),
        ) as client:
            self.assertEqual(client.get('/api/private').status_code, 503)

        application = _protected_app({'GOODJOB_SHARED_TOKEN': TOKEN})
        with TestClient(
            application,
            base_url='https://backend.example',
            client=('203.0.113.10', 50000),
        ) as client:
            missing = client.get('/api/private')
            wrong = client.get('/api/private', headers={'Authorization': f'Bearer {"x" * 32}'})
            accepted = client.get('/api/private', headers={'Authorization': f'Bearer {TOKEN}'})

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.headers.get('www-authenticate'), 'Bearer')
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()['authMethod'], 'bearer')

    def test_untrusted_forwarded_headers_are_ignored(self) -> None:
        application = _protected_app({'GOODJOB_SHARED_TOKEN': TOKEN})
        forwarded = {
            'X-Forwarded-For': '192.168.1.20',
            'X-Forwarded-Proto': 'https',
            'Authorization': f'Bearer {TOKEN}',
        }
        with TestClient(application, client=('203.0.113.10', 50000)) as client:
            response = client.get('/api/private', headers=forwarded)
        self.assertEqual(response.status_code, 426)

    def test_untrusted_lan_peer_cannot_use_forwarded_headers_and_keep_lan_exemption(self) -> None:
        application = _protected_app({'GOODJOB_SHARED_TOKEN': TOKEN})
        header_sets = (
            {'X-Forwarded-For': '203.0.113.10'},
            {'X-Forwarded-Proto': 'https'},
            {
                'X-Forwarded-For': '203.0.113.10',
                'X-Forwarded-Proto': 'https',
                'Authorization': f'Bearer {TOKEN}',
            },
        )
        with TestClient(application, client=('127.0.0.1', 50000)) as client:
            for headers in header_sets:
                with self.subTest(headers=headers):
                    response = client.get('/api/private', headers=headers)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn('trusted proxy', response.json()['detail'].lower())

    def test_trusted_proxy_resolves_public_client_and_https(self) -> None:
        application = _protected_app(
            {
                'GOODJOB_SHARED_TOKEN': TOKEN,
                'GOODJOB_TRUSTED_PROXIES': '127.0.0.0/8',
            }
        )
        forwarded = {
            'X-Forwarded-For': '203.0.113.10',
            'X-Forwarded-Proto': 'https',
        }
        with TestClient(application, client=('127.0.0.1', 50000)) as client:
            denied = client.get('/api/private', headers=forwarded)
            accepted = client.get(
                '/api/private',
                headers={**forwarded, 'Authorization': f'Bearer {TOKEN}'},
            )
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()['clientHost'], '203.0.113.10')

    def test_proxy_chain_stops_at_nearest_untrusted_hop(self) -> None:
        application = _protected_app(
            {
                'GOODJOB_SHARED_TOKEN': TOKEN,
                'GOODJOB_TRUSTED_PROXIES': '127.0.0.0/8',
            }
        )
        with TestClient(application, client=('127.0.0.1', 50000)) as client:
            response = client.get(
                '/api/private',
                headers={
                    'X-Forwarded-For': '192.168.1.20, 198.51.100.20',
                    'X-Forwarded-Proto': 'https',
                },
            )
        self.assertEqual(response.status_code, 401)

    def test_trusted_proxy_chain_supports_lan_exemption_and_public_bearer_auth(self) -> None:
        application = _protected_app(
            {
                'GOODJOB_SHARED_TOKEN': TOKEN,
                'GOODJOB_TRUSTED_PROXIES': '127.0.0.0/8, 10.0.0.0/8',
            }
        )
        with TestClient(application, client=('127.0.0.1', 50000)) as client:
            lan = client.get(
                '/api/private',
                headers={
                    'X-Forwarded-For': '192.168.1.20, 10.0.0.8',
                    'X-Forwarded-Proto': 'http',
                },
            )
            public = client.get(
                '/api/private',
                headers={
                    'X-Forwarded-For': '203.0.113.10, 10.0.0.8',
                    'X-Forwarded-Proto': 'https',
                    'Authorization': f'Bearer {TOKEN}',
                },
            )

        self.assertEqual(lan.status_code, 200)
        self.assertEqual(lan.json(), {'authMethod': 'local-network', 'clientHost': '192.168.1.20'})
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json(), {'authMethod': 'bearer', 'clientHost': '203.0.113.10'})

    def test_invalid_headers_from_trusted_proxy_fail_closed(self) -> None:
        application = _protected_app({'GOODJOB_TRUSTED_PROXIES': '127.0.0.1'})
        with TestClient(application, client=('127.0.0.1', 50000)) as client:
            missing_both = client.get('/api/private')
            missing_for = client.get('/api/private', headers={'X-Forwarded-Proto': 'https'})
            missing_proto = client.get(
                '/api/private',
                headers={'X-Forwarded-For': '203.0.113.10'},
            )
            invalid_for = client.get(
                '/api/private',
                headers={
                    'X-Forwarded-For': 'not-an-ip',
                    'X-Forwarded-Proto': 'https',
                },
            )
            ambiguous_proto = client.get(
                '/api/private',
                headers={
                    'X-Forwarded-For': '203.0.113.10',
                    'X-Forwarded-Proto': 'https, http',
                },
            )
            duplicate_for = client.get(
                '/api/private',
                headers=[
                    ('X-Forwarded-For', '203.0.113.10'),
                    ('X-Forwarded-For', '192.168.1.20'),
                    ('X-Forwarded-Proto', 'https'),
                ],
            )
            duplicate_proto = client.get(
                '/api/private',
                headers=[
                    ('X-Forwarded-For', '203.0.113.10'),
                    ('X-Forwarded-Proto', 'https'),
                    ('X-Forwarded-Proto', 'http'),
                ],
            )
        self.assertEqual(missing_both.status_code, 400)
        self.assertEqual(missing_for.status_code, 400)
        self.assertEqual(missing_proto.status_code, 400)
        self.assertEqual(invalid_for.status_code, 400)
        self.assertEqual(ambiguous_proto.status_code, 400)
        self.assertEqual(duplicate_for.status_code, 400)
        self.assertEqual(duplicate_proto.status_code, 400)

    def test_authenticated_public_admin_request_is_not_rejected_twice(self) -> None:
        application = _protected_app({'GOODJOB_SHARED_TOKEN': TOKEN})
        with TestClient(
            application,
            base_url='https://backend.example',
            client=('203.0.113.10', 50000),
        ) as client:
            response = client.get(
                '/api/admin-test',
                headers={'Authorization': f'Bearer {TOKEN}'},
            )
        self.assertEqual(response.status_code, 200)


class ConnectionRouteTests(unittest.TestCase):
    def test_connection_probe_is_protected_and_returns_protocol_metadata(self) -> None:
        application = main.create_app()
        with TestClient(application, client=('127.0.0.1', 50000)) as client:
            response = client.get('/api/connection')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['ok'], True)
        self.assertEqual(payload['connected'], True)
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['scriptApiVersion'], 2)
        self.assertEqual(payload['protocolVersion'], 2)
        datetime.fromisoformat(payload['serverTime'])


if __name__ == '__main__':
    unittest.main()
