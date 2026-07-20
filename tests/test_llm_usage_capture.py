from __future__ import annotations

import asyncio
from datetime import datetime
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from app.llm.gateway import LLMGateway, LLMGatewayError, extract_usage
from app.llm.manager import LLMManager, LLMProvider


def _config(**overrides: object) -> dict:
    config = {
        'provider_id': 'provider-1',
        'provider_name': 'Primary',
        'api_base': 'https://llm.example/v1',
        'api_key': 'secret',
        'model': 'configured-model',
        'timeout': 1,
        'retry_count': 0,
        'retry_base_delay': 0,
        'retry_max_delay': 0,
        'min_request_interval': 0,
        'cache_ttl_seconds': 0,
        'circuit_failure_threshold': 10,
    }
    config.update(overrides)
    return config


class _FakeClient:
    def __init__(self, responses: list[object]):
        self.responses = responses
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        value = self.responses[self.calls]
        self.calls += 1
        if isinstance(value, BaseException):
            raise value
        return value


class _BlockingClient(_FakeClient):
    def __init__(self, response: httpx.Response):
        super().__init__([response])
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def post(self, *args, **kwargs):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.responses[0]


def _client_factory(client: _FakeClient):
    return lambda timeout, proxy: client


def _response(payload: object, status_code: int = 200) -> httpx.Response:
    request = httpx.Request('POST', 'https://llm.example/v1/chat/completions')
    return httpx.Response(status_code, json=payload, request=request)


class UsageParserTests(unittest.TestCase):
    def test_accepts_openai_and_input_output_usage_aliases(self) -> None:
        self.assertEqual(
            extract_usage({'usage': {'prompt_tokens': 3, 'completion_tokens': 4}}),
            (True, 3, 4, 7),
        )
        self.assertEqual(
            extract_usage({'usage': {'input_tokens': 5, 'output_tokens': 6, 'total_tokens': 12}}),
            (True, 5, 6, 12),
        )

    def test_ignores_invalid_usage_and_reports_missing_usage(self) -> None:
        self.assertEqual(
            extract_usage({'usage': {'prompt_tokens': -1, 'completion_tokens': True}}),
            (False, 0, 0, 0),
        )
        self.assertEqual(extract_usage({'choices': []}), (False, 0, 0, 0))


class GatewayUsageCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_attempt_observer_receives_usage_and_response_model(self) -> None:
        client = _FakeClient([
            _response({
                'model': 'served-model',
                'usage': {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5},
                'choices': [{'message': {'content': 'hello'}}],
            })
        ])
        events: list[dict] = []
        gateway = LLMGateway(_client_factory(client))

        result = await gateway.chat_completions(
            _config(),
            {'messages': [{'role': 'user', 'content': 'hi'}]},
            'job_filter',
            attempt_observer=events.append,
        )

        self.assertEqual(result['model'], 'served-model')
        self.assertEqual(len(events), 1)
        self.assertEqual(
            {key: events[0][key] for key in (
                'provider_id', 'provider_name', 'api_base', 'model', 'purpose',
                'success', 'usage_reported', 'input_tokens', 'output_tokens', 'total_tokens',
            )},
            {
                'provider_id': 'provider-1',
                'provider_name': 'Primary',
                'api_base': 'https://llm.example/v1',
                'model': 'served-model',
                'purpose': 'job_filter',
                'success': True,
                'usage_reported': True,
                'input_tokens': 2,
                'output_tokens': 3,
                'total_tokens': 5,
            },
        )
        self.assertIsInstance(events[0]['occurred_at'], datetime)

    async def test_invalid_success_body_records_usage_without_changing_retry_policy(self) -> None:
        client = _FakeClient([
            _response({'usage': {'prompt_tokens': 1, 'completion_tokens': 2}, 'choices': []}),
            _response({'choices': [{'message': {'content': 'unexpected retry'}}]}),
        ])
        events: list[dict] = []
        gateway = LLMGateway(_client_factory(client))

        with self.assertRaises(LLMGatewayError):
            await gateway.chat_completions(
                _config(retry_count=1),
                {'messages': [{'role': 'user', 'content': 'hi'}]},
                'introduce',
                attempt_observer=events.append,
            )

        self.assertEqual(client.calls, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(
            (events[0]['success'], events[0]['usage_reported'], events[0]['total_tokens']),
            (False, True, 3),
        )

    async def test_retryable_http_error_and_success_are_recorded_per_attempt(self) -> None:
        client = _FakeClient([
            _response({'usage': {'prompt_tokens': 1, 'completion_tokens': 2}}, status_code=503),
            _response({
                'usage': {'input_tokens': 4, 'output_tokens': 5},
                'choices': [{'message': {'content': 'ok'}}],
            }),
        ])
        events: list[dict] = []
        gateway = LLMGateway(_client_factory(client))

        result = await gateway.chat_completions(
            _config(retry_count=1),
            {'messages': [{'role': 'user', 'content': 'hi'}]},
            'introduce',
            attempt_observer=events.append,
        )

        self.assertEqual(result['choices'][0]['message']['content'], 'ok')
        self.assertEqual(client.calls, 2)
        self.assertEqual(len(events), 2)
        self.assertEqual(
            [(event['success'], event['usage_reported'], event['total_tokens']) for event in events],
            [(False, True, 3), (True, True, 9)],
        )

    async def test_retry_failure_is_observed_before_backoff_sleep(self) -> None:
        client = _FakeClient([
            _response({'usage': {'prompt_tokens': 1, 'completion_tokens': 2}}, status_code=503),
            _response({
                'usage': {'prompt_tokens': 2, 'completion_tokens': 2},
                'choices': [{'message': {'content': 'ok'}}],
            }),
        ])
        events: list[dict] = []
        sleep_event_counts: list[int] = []
        gateway = LLMGateway(_client_factory(client))

        async def fake_sleep(_delay: float) -> None:
            sleep_event_counts.append(len(events))

        with patch('app.llm.gateway.asyncio.sleep', side_effect=fake_sleep):
            await gateway.chat_completions(
                _config(retry_count=1),
                {'messages': [{'role': 'user', 'content': 'hi'}]},
                'introduce',
                attempt_observer=events.append,
            )

        self.assertEqual(sleep_event_counts, [1])

    async def test_network_exception_records_one_failed_attempt_without_usage(self) -> None:
        request = httpx.Request('POST', 'https://llm.example/v1/chat/completions')
        client = _FakeClient([httpx.ConnectError('offline', request=request)])
        events: list[dict] = []
        gateway = LLMGateway(_client_factory(client))

        with self.assertRaises(LLMGatewayError):
            await gateway.chat_completions(
                _config(),
                {'messages': [{'role': 'user', 'content': 'hi'}]},
                'job_filter',
                attempt_observer=events.append,
            )

        self.assertEqual(client.calls, 1)
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]['success'])
        self.assertFalse(events[0]['usage_reported'])
        self.assertEqual(events[0]['total_tokens'], 0)

    async def test_cache_hit_does_not_emit_a_second_attempt(self) -> None:
        client = _FakeClient([
            _response({
                'usage': {'prompt_tokens': 2, 'completion_tokens': 3},
                'choices': [{'message': {'content': 'cached'}}],
            })
        ])
        events: list[dict] = []
        gateway = LLMGateway(_client_factory(client))
        config = _config(cache_ttl_seconds=60)
        payload = {'messages': [{'role': 'user', 'content': 'same'}]}

        await gateway.chat_completions(config, payload, 'introduce', attempt_observer=events.append)
        await gateway.chat_completions(config, payload, 'introduce', attempt_observer=events.append)

        self.assertEqual(client.calls, 1)
        self.assertEqual(len(events), 1)

    async def test_concurrent_inflight_callers_share_one_recorded_attempt(self) -> None:
        client = _BlockingClient(_response({
            'usage': {'prompt_tokens': 2, 'completion_tokens': 3},
            'choices': [{'message': {'content': 'shared'}}],
        }))
        events: list[dict] = []
        gateway = LLMGateway(_client_factory(client))
        config = _config(cache_ttl_seconds=60)
        payload = {'messages': [{'role': 'user', 'content': 'same'}]}

        first = asyncio.create_task(
            gateway.chat_completions(config, payload, 'job_filter', attempt_observer=events.append)
        )
        await client.started.wait()
        second = asyncio.create_task(
            gateway.chat_completions(config, payload, 'job_filter', attempt_observer=events.append)
        )
        await asyncio.sleep(0)
        client.release.set()
        first_result, second_result = await asyncio.gather(first, second)

        self.assertIs(first_result, second_result)
        self.assertEqual(client.calls, 1)
        self.assertEqual(len(events), 1)


class ManagerUsageCaptureTests(unittest.IsolatedAsyncioTestCase):
    def test_provider_id_is_carried_into_gateway_config(self) -> None:
        provider = LLMProvider(
            provider_id='provider-42',
            index=1,
            name='Named',
            api_base='https://llm.example/v1',
            api_key='secret',
            model='model',
            timeout=10,
        )
        config = provider.gateway_config()
        self.assertEqual(config['provider_id'], 'provider-42')
        self.assertEqual(config['provider_name'], 'Named')

    async def test_manager_observer_records_current_provider_and_fails_open(self) -> None:
        manager = object.__new__(LLMManager)
        manager._lock = __import__('threading').Lock()
        manager._providers = []
        manager._strategy = 'failover'
        manager._job_filter = False
        manager._round_robin = iter(())
        manager._usage_warning_at = 0.0
        provider = LLMProvider(
            provider_id='provider-42',
            index=1,
            name='Named',
            api_base='https://llm.example/v1',
            api_key='secret',
            model='model',
            timeout=10,
        )
        provider.gateway = Mock()
        provider.gateway.chat_completions = AsyncMock(return_value={'choices': []})
        manager._providers = [provider]
        recorder = Mock(side_effect=RuntimeError('storage unavailable'))
        manager.set_usage_recorder(recorder)

        result = await manager.chat_completions({'messages': []}, 'job_filter')

        self.assertEqual(result, {'choices': []})
        provider.gateway.chat_completions.assert_awaited_once()
        observer = provider.gateway.chat_completions.await_args.kwargs['attempt_observer']
        observer({
            'provider_id': 'provider-42', 'provider_name': 'Named', 'api_base': 'https://llm.example/v1',
            'model': 'model', 'purpose': 'job_filter', 'success': True, 'usage_reported': False,
            'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0,
            'occurred_at': datetime.now().astimezone(),
        })
        recorder.assert_called_once()

    async def test_unsaved_provider_health_test_returns_provider_id_and_records_connection_test(self) -> None:
        target = {
            'provider_id': 'provider-unsaved',
            'name': 'Temporary',
            'api_base': 'https://llm.example/v1',
            'api_key': 'secret',
            'model': 'model',
            'proxy_enabled': False,
            'proxy_url': '',
        }
        response = _response({
            'model': 'served-model',
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1},
            'choices': [{'message': {'content': 'pong'}}],
        })
        recorder = Mock()
        with patch('app.llm.manager.httpx.AsyncClient') as client_type:
            client = client_type.return_value
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client.post = AsyncMock(return_value=response)
            manager = object.__new__(LLMManager)
            manager._lock = __import__('threading').Lock()
            manager._usage_recorder = recorder
            manager._usage_warning_at = 0.0
            result = await manager._test_provider_config(
                target,
                10,
                usage_recorder=manager._record_usage_event,
            )

        self.assertTrue(result['ok'])
        self.assertEqual(result['providerId'], 'provider-unsaved')
        self.assertEqual(result['model'], 'served-model')
        recorder.assert_called_once()
        event = recorder.call_args.kwargs
        self.assertEqual(event['purpose'], 'connection_test')
        self.assertTrue(event['success'])
        self.assertEqual(event['total_tokens'], 2)


if __name__ == '__main__':
    unittest.main()
