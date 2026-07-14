import asyncio
import unittest

import httpx

from llm_gateway import LLMGateway, LLMGatewayError


def config(**overrides):
    return {
        'api_base': 'https://llm.example/v1',
        'api_key': 'test-key',
        'model': 'test-model',
        'timeout': 2,
        'max_concurrent_requests': 1,
        'min_request_interval': 0,
        'retry_count': 2,
        'retry_base_delay': 0,
        'retry_max_delay': 0,
        'circuit_failure_threshold': 3,
        'circuit_open_seconds': 60,
        'cache_ttl_seconds': 60,
        **overrides,
    }


def gateway_with_handler(handler):
    transport = httpx.MockTransport(handler)
    return LLMGateway(lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout))


class LLMGatewayTest(unittest.TestCase):
    def test_retries_retryable_500_then_succeeds(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return httpx.Response(500, json={'error': 'busy'})
            return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

        gateway = gateway_with_handler(handler)
        result = asyncio.run(gateway.chat_completions(config(), {'messages': []}, 'introduce'))

        self.assertEqual(result['choices'][0]['message']['content'], 'ok')
        self.assertEqual(attempts, 3)
        self.assertEqual(gateway.snapshot()['retries'], 2)

    def test_circuit_opens_and_rejects_without_calling_upstream(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(500, json={'error': 'busy'})

        gateway = gateway_with_handler(handler)
        cfg = config(retry_count=0, circuit_failure_threshold=2)

        async def run():
            for _ in range(2):
                with self.assertRaises(LLMGatewayError):
                    await gateway.chat_completions(cfg, {'messages': [{'content': str(_)}]}, 'introduce')
            with self.assertRaises(LLMGatewayError) as caught:
                await gateway.chat_completions(cfg, {'messages': [{'content': 'third'}]}, 'introduce')
            self.assertTrue(caught.exception.circuit_open)

        asyncio.run(run())
        self.assertEqual(attempts, 2)
        self.assertEqual(gateway.snapshot()['state'], 'open')

    def test_identical_concurrent_requests_are_coalesced(self):
        attempts = 0

        async def async_response():
            await asyncio.sleep(0.02)

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, *args, **kwargs):
                nonlocal attempts
                attempts += 1
                await async_response()
                request = httpx.Request('POST', 'https://llm.example/v1/chat/completions')
                return httpx.Response(200, request=request, json={'choices': [{'message': {'content': 'ok'}}]})

        gateway = LLMGateway(lambda timeout: Client())

        async def run():
            payload = {'messages': [{'content': 'same'}]}
            return await asyncio.gather(*[
                gateway.chat_completions(config(), payload, 'introduce') for _ in range(12)
            ])

        results = asyncio.run(run())
        self.assertEqual(len(results), 12)
        self.assertEqual(attempts, 1)

    def test_reasoning_field_without_content_is_a_valid_provider_response(self):
        def handler(request):
            return httpx.Response(200, json={
                'choices': [{
                    'finish_reason': 'length',
                    'message': {'content': None, 'reasoning': '正在组织最终招呼语'},
                }],
            })

        gateway = gateway_with_handler(handler)
        result = asyncio.run(gateway.chat_completions(config(), {'messages': []}, 'introduce'))

        self.assertEqual(result['choices'][0]['message']['reasoning'], '正在组织最终招呼语')
        self.assertEqual(gateway.snapshot()['state'], 'closed')
        self.assertEqual(gateway.snapshot()['failures'], 0)

    def test_reasoning_only_response_is_not_cached(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(200, json={
                'choices': [{
                    'finish_reason': 'stop',
                    'message': {'content': None, 'reasoning': 'still reasoning'},
                }],
            })

        gateway = gateway_with_handler(handler)

        async def run():
            payload = {'messages': [{'content': 'same'}]}
            await gateway.chat_completions(config(), payload, 'introduce')
            await gateway.chat_completions(config(), payload, 'introduce')

        asyncio.run(run())
        self.assertEqual(attempts, 2)
        self.assertEqual(gateway.snapshot()['cacheSize'], 0)

    def test_content_parts_are_accepted_and_cached(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(200, json={
                'choices': [{
                    'finish_reason': 'stop',
                    'message': {'content': [{'type': 'text', 'text': 'hello'}]},
                }],
            })

        gateway = gateway_with_handler(handler)

        async def run():
            payload = {'messages': [{'content': 'same'}]}
            first = await gateway.chat_completions(config(), payload, 'introduce')
            second = await gateway.chat_completions(config(), payload, 'introduce')
            return first, second

        first, second = asyncio.run(run())
        self.assertEqual(first, second)
        self.assertEqual(attempts, 1)
        self.assertEqual(gateway.snapshot()['cacheSize'], 1)


if __name__ == '__main__':
    unittest.main()
