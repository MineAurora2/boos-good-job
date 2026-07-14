"""验证 LLM 代理配置的持久化、脱敏、迁移和实际请求连接语义。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

import llm_env_store
from llm_gateway import LLMGateway, LLMGatewayError
from llm_manager import LLMManager


class _FakeClient:
    """实现 httpx 异步上下文协议的最小假客户端，避免测试访问真实网络。"""

    def __init__(self, response: httpx.Response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, **kwargs):
        return self.response


def _response(url: str = 'https://api.example.com/v1/chat/completions') -> httpx.Response:
    """构造一份带原始 Request 的成功响应，供 httpx 状态检查使用。"""
    return httpx.Response(
        200,
        request=httpx.Request('POST', url),
        json={
            'model': 'test-model',
            'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}],
        },
    )


class LLMProxyStoreTests(unittest.TestCase):
    """覆盖 .env 代理字段的保存、脱敏、兼容迁移与输入校验。"""

    def setUp(self):
        """把配置路径和 GOODJOB_LLM 环境变量隔离到测试专用文件。"""
        self.original_path = llm_env_store.ENV_PATH
        llm_env_store.ENV_PATH = Path(__file__).with_name('.test-llm.env')
        llm_env_store.ENV_PATH.unlink(missing_ok=True)
        llm_env_store.ENV_PATH.with_name('.env.tmp').unlink(missing_ok=True)
        self.original_env = {
            key: value for key, value in os.environ.items() if key.startswith('GOODJOB_LLM')
        }
        for key in list(os.environ):
            if key.startswith('GOODJOB_LLM'):
                os.environ.pop(key, None)

    def tearDown(self):
        """恢复进程环境并删除临时配置，避免测试污染开发者的真实 .env。"""
        for key in list(os.environ):
            if key.startswith('GOODJOB_LLM'):
                os.environ.pop(key, None)
        os.environ.update(self.original_env)
        llm_env_store.ENV_PATH.unlink(missing_ok=True)
        llm_env_store.ENV_PATH.with_name('.env.tmp').unlink(missing_ok=True)
        llm_env_store.ENV_PATH = self.original_path

    @staticmethod
    def _payload(**provider_overrides):
        """创建一份有效配置，允许每个测试只覆盖关注字段。"""
        provider = {
            'name': 'primary',
            'api_base': 'https://api.example.com/v1',
            'api_key': 'sk-test-secret',
            'model': 'test-model',
            'proxy_url': 'http://proxy-user:proxy-pass@127.0.0.1:7890',
            'proxy_enabled': True,
            'enabled': True,
        }
        provider.update(provider_overrides)
        return {
            'strategy': 'failover',
            'timeout': 30,
            'jobFilter': False,
            'providers': [provider],
        }

    @staticmethod
    def _clear_llm_env():
        """清空同步到进程的配置，强制下一次读取测试文件内容。"""
        for key in list(os.environ):
            if key.startswith('GOODJOB_LLM'):
                os.environ.pop(key, None)

    def test_proxy_round_trip_masks_credentials_and_keeps_secret(self):
        """公开响应应脱敏，KEEP_SECRET 再保存时仍保留原始凭据。"""
        public = llm_env_store.save_llm_config(self._payload())
        self.assertTrue(public['providers'][0]['proxyUrlConfigured'])
        self.assertEqual(
            public['providers'][0]['proxyUrlMasked'],
            'http://******@127.0.0.1:7890',
        )
        self.assertNotIn('proxy-pass', str(public))
        self.assertEqual(
            llm_env_store._mask_proxy_url('http://secret-token@127.0.0.1:7890'),
            'http://******@127.0.0.1:7890',
        )
        self.assertNotIn('proxy_url', public['providers'][0])
        saved_text = llm_env_store.ENV_PATH.read_text(encoding='utf-8')
        self.assertIn('GOODJOB_LLM_1_PROXY_URL=http://proxy-user:proxy-pass@127.0.0.1:7890', saved_text)
        self.assertIn('GOODJOB_LLM_1_PROXY_ENABLED=true', saved_text)
        self._clear_llm_env()
        self.assertTrue(llm_env_store.load_llm_config()['providers'][0]['proxy_enabled'])

        kept = self._payload(
            index=1,
            api_key=llm_env_store.KEEP_SECRET,
            proxy_url=llm_env_store.KEEP_SECRET,
            proxy_enabled=False,
        )
        llm_env_store.save_llm_config(kept)
        self._clear_llm_env()
        raw = llm_env_store.load_llm_config()['providers'][0]
        self.assertEqual(raw['api_key'], 'sk-test-secret')
        self.assertEqual(raw['proxy_url'], 'http://proxy-user:proxy-pass@127.0.0.1:7890')
        self.assertFalse(raw['proxy_enabled'])

    def test_legacy_unnumbered_proxy_loads_and_migrates(self):
        """旧版无编号变量可读取，并在保存后迁移到编号格式。"""
        os.environ.update({
            'GOODJOB_LLM_API_BASE': 'https://legacy.example.com/v1',
            'GOODJOB_LLM_API_KEY': 'legacy-key',
            'GOODJOB_LLM_MODEL': 'legacy-model',
            'GOODJOB_LLM_PROXY_URL': 'http://127.0.0.1:8080',
            'GOODJOB_LLM_PROXY_ENABLED': 'true',
        })
        legacy = llm_env_store.load_llm_config()['providers'][0]
        self.assertEqual(legacy['index'], 1)
        self.assertTrue(legacy['proxy_enabled'])
        self.assertEqual(legacy['proxy_url'], 'http://127.0.0.1:8080')

        llm_env_store.save_llm_config({
            'strategy': 'failover',
            'timeout': 30,
            'jobFilter': False,
            'providers': [{
                'index': 1,
                'name': legacy['name'],
                'api_base': legacy['api_base'],
                'api_key': llm_env_store.KEEP_SECRET,
                'model': legacy['model'],
                'proxy_url': llm_env_store.KEEP_SECRET,
                'proxy_enabled': legacy['proxy_enabled'],
                'enabled': True,
            }],
        })
        keys = {
            line.split('=', 1)[0]
            for line in llm_env_store.ENV_PATH.read_text(encoding='utf-8').splitlines()
            if '=' in line and not line.lstrip().startswith('#')
        }
        self.assertIn('GOODJOB_LLM_1_PROXY_URL', keys)
        self.assertNotIn('GOODJOB_LLM_PROXY_URL', keys)
        self.assertNotIn('GOODJOB_LLM_API_BASE', os.environ)

    def test_legacy_payload_does_not_turn_a_saved_proxy_back_on(self):
        """旧前端未提交代理开关时，不应把已关闭但已保存的代理重新开启。"""
        llm_env_store.save_llm_config(self._payload(proxy_enabled=False))
        legacy = self._payload(index=1, api_key=llm_env_store.KEEP_SECRET)
        legacy['providers'][0].pop('proxy_url')
        legacy['providers'][0].pop('proxy_enabled')
        llm_env_store.save_llm_config(legacy)
        raw = llm_env_store.load_llm_config()['providers'][0]
        self.assertFalse(raw['proxy_enabled'])
        self.assertTrue(raw['proxy_url'])

    def test_rejects_non_http_proxy_and_allows_disabled_draft(self):
        """启用接口仅接受 HTTP(S) 代理，停用草稿允许暂时缺少必填项。"""
        with self.assertRaisesRegex(ValueError, r'HTTP\(S\)'):
            llm_env_store.save_llm_config(self._payload(proxy_url='socks5://127.0.0.1:1080'))
        public = llm_env_store.save_llm_config(
            self._payload(
                api_base='',
                api_key='',
                model='',
                proxy_url='',
                proxy_enabled=True,
                enabled=False,
            )
        )
        self.assertFalse(public['providers'][0]['enabled'])


class LLMProxyRequestTests(unittest.IsolatedAsyncioTestCase):
    """验证正式请求和测活请求向 httpx 传入一致的代理参数。"""

    async def test_gateway_passes_proxy_and_disables_environment_proxy(self):
        """开启代理时显式传入 URL，同时禁止继承系统环境代理。"""
        captured = {}

        def client_factory(**kwargs):
            captured.update(kwargs)
            return _FakeClient(_response())

        config = {
            'api_base': 'https://api.example.com/v1',
            'api_key': 'sk-test',
            'model': 'test-model',
            'proxy_url': 'http://127.0.0.1:7890',
            'proxy_enabled': True,
            'timeout': 10,
            'retry_count': 0,
            'cache_ttl_seconds': 0,
            'min_request_interval': 0,
        }
        with patch('llm_gateway.httpx.AsyncClient', side_effect=client_factory):
            result = await LLMGateway().chat_completions(
                config,
                {'model': 'test-model', 'messages': [{'role': 'user', 'content': 'ping'}]},
                'test',
            )
        self.assertEqual(result['choices'][0]['message']['content'], 'ok')
        self.assertEqual(captured['proxy'], 'http://127.0.0.1:7890')
        self.assertFalse(captured['trust_env'])

    async def test_gateway_proxy_switch_off_forces_direct_connection(self):
        """关闭代理时即使保留 URL，也必须向 httpx 传入 None 强制直连。"""
        captured = {}

        def client_factory(**kwargs):
            captured.update(kwargs)
            return _FakeClient(_response())

        config = {
            'api_base': 'https://api.example.com/v1',
            'api_key': 'sk-test',
            'model': 'test-model',
            'proxy_url': 'http://127.0.0.1:7890',
            'proxy_enabled': False,
            'retry_count': 0,
            'cache_ttl_seconds': 0,
            'min_request_interval': 0,
        }
        with patch('llm_gateway.httpx.AsyncClient', side_effect=client_factory):
            await LLMGateway().chat_completions(config, {'messages': []}, 'direct-test')
        self.assertIsNone(captured['proxy'])
        self.assertFalse(captured['trust_env'])

    async def test_manager_reload_passes_provider_proxy_to_gateway(self):
        """管理器重载后应把 provider 的代理配置完整传给独占网关。"""
        captured = {}

        class CaptureGateway:
            async def chat_completions(self, config, payload, purpose):
                captured.update(config)
                return {'choices': [{'message': {'content': 'ok'}}]}

            def snapshot(self):
                return {'state': 'closed'}

            def reset_circuit(self, clear_cache=False):
                return None

        loaded = {
            'strategy': 'failover',
            'timeout': 30,
            'jobFilter': False,
            'providers': [{
                'index': 1,
                'name': 'primary',
                'api_base': 'https://api.example.com/v1',
                'api_key': 'sk-test',
                'model': 'test-model',
                'proxy_url': 'http://127.0.0.1:7890',
                'proxy_enabled': True,
                'enabled': True,
            }],
        }
        with patch('llm_manager.llm_env_store.load_llm_config', return_value=loaded), patch('llm_manager.LLMGateway', side_effect=CaptureGateway):
            manager = LLMManager()
        await manager.chat_completions({'messages': []}, 'manager-test')
        self.assertEqual(captured['proxy_url'], 'http://127.0.0.1:7890')
        self.assertTrue(captured['proxy_enabled'])

    async def test_manager_reload_reuses_unchanged_provider_gateway(self):
        """仅调整调度配置时应保留原网关的在途请求、缓存和熔断状态。"""
        loaded = {
            'strategy': 'failover',
            'timeout': 30,
            'jobFilter': False,
            'providers': [{
                'index': 1,
                'name': 'primary',
                'api_base': 'https://api.example.com/v1',
                'api_key': 'sk-test',
                'model': 'test-model',
                'proxy_url': '',
                'proxy_enabled': False,
                'enabled': True,
            }],
        }
        with patch('llm_manager.llm_env_store.load_llm_config', return_value=loaded), patch(
            'llm_manager.LLMGateway',
            wraps=LLMGateway,
        ) as gateway_factory:
            manager = LLMManager()
            original_gateway = manager._providers[0].gateway
            loaded['strategy'] = 'round_robin'
            loaded['jobFilter'] = True
            manager.reload()

        self.assertIs(manager._providers[0].gateway, original_gateway)
        self.assertEqual(gateway_factory.call_count, 1)

    async def test_gateway_wraps_invalid_proxy_factory_errors(self):
        """客户端创建阶段的代理错误应统一包装成网关异常。"""
        gateway = LLMGateway(client_factory=lambda timeout, proxy: (_ for _ in ()).throw(ValueError('bad proxy')))
        config = {
            'api_base': 'https://api.example.com/v1',
            'api_key': 'sk-test',
            'model': 'test-model',
            'proxy_url': 'bad-proxy',
            'proxy_enabled': True,
            'retry_count': 0,
            'cache_ttl_seconds': 0,
            'min_request_interval': 0,
        }
        with self.assertRaises(LLMGatewayError):
            await gateway.chat_completions(config, {'messages': []}, 'test')

    async def test_cancelled_owner_keeps_inflight_request_for_later_waiters(self):
        """取消首个等待者不能让仍在执行的同请求再次发起并重复计费。"""
        gateway = LLMGateway()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def request(_config, _payload):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {
                'choices': [{
                    'message': {'content': 'ok'},
                    'finish_reason': 'stop',
                }],
            }

        config = {
            'api_base': 'https://api.example.com/v1',
            'model': 'test-model',
            'cache_ttl_seconds': 60,
        }
        payload = {'messages': [{'role': 'user', 'content': 'same request'}]}
        with patch.object(gateway, '_request', side_effect=request):
            owner = asyncio.create_task(gateway.chat_completions(config, payload, 'cancel-test'))
            await started.wait()
            owner.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await owner

            follower = asyncio.create_task(gateway.chat_completions(config, payload, 'cancel-test'))
            await asyncio.sleep(0)
            self.assertEqual(calls, 1)
            release.set()
            result = await follower
            await asyncio.sleep(0)

        self.assertEqual(result['choices'][0]['message']['content'], 'ok')
        self.assertFalse(gateway._inflight)

    async def test_health_check_uses_the_same_proxy_settings(self):
        """接口测活必须复用正式请求的显式代理和环境隔离规则。"""
        captured = {}

        def client_factory(**kwargs):
            captured.update(kwargs)
            return _FakeClient(_response())

        target = {
            'api_base': 'https://api.example.com/v1',
            'api_key': 'sk-test',
            'model': 'test-model',
            'proxy_url': 'http://127.0.0.1:7890',
            'proxy_enabled': True,
        }
        with patch('llm_manager.httpx.AsyncClient', side_effect=client_factory):
            result = await LLMManager._test_provider_config(target, 10)
        self.assertTrue(result['ok'])
        self.assertTrue(result['viaProxy'])
        self.assertEqual(captured['proxy'], 'http://127.0.0.1:7890')
        self.assertFalse(captured['trust_env'])

    async def test_health_check_proxy_switch_off_forces_direct_connection(self):
        """测活在代理关闭时也必须真正直连，而非回退到系统代理。"""
        captured = {}

        def client_factory(**kwargs):
            captured.update(kwargs)
            return _FakeClient(_response())

        target = {
            'api_base': 'https://api.example.com/v1',
            'api_key': 'sk-test',
            'model': 'test-model',
            'proxy_url': 'http://127.0.0.1:7890',
            'proxy_enabled': False,
        }
        with patch('llm_manager.httpx.AsyncClient', side_effect=client_factory):
            result = await LLMManager._test_provider_config(target, 10)
        self.assertTrue(result['ok'])
        self.assertFalse(result['viaProxy'])
        self.assertIsNone(captured['proxy'])
        self.assertFalse(captured['trust_env'])


if __name__ == '__main__':
    unittest.main()
