"""多大模型接口管理器：调度、故障转移、测活。

每个接口（provider）独占一个 LLMGateway 实例，因此天然获得 per-provider 的
熔断、重试、缓存与并发控制。管理器只负责“选哪个 provider”与“失败后跳到下一个”。

调度策略：
- failover     故障转移：固定用第一个可用接口，熔断/连续失败后才切到下一个。
- round_robin  轮询：每次请求轮转到下一个可用接口，做负载分摊。
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import time

import httpx

from app.llm.gateway import LLMGateway, LLMGatewayError
from app.llm import env_store as llm_env_store


# 从 .env 剥离出来的调优参数改为进程内默认常量（不再暴露到配置面板）。
_GATEWAY_DEFAULTS = {
    'max_concurrent_requests': 2,
    'min_request_interval': 0.5,
    'retry_count': 2,
    'retry_base_delay': 1.0,
    'retry_max_delay': 8.0,
    'circuit_failure_threshold': 3,
    'circuit_open_seconds': 60,
    'cache_ttl_seconds': 1800,
}


class LLMProvider:
    """单个接口：配置 + 独占网关。"""

    def __init__(
        self,
        index: int,
        name: str,
        api_base: str,
        api_key: str,
        model: str,
        timeout: int,
        proxy_url: str = '',
        proxy_enabled: bool = False,
    ):
        self.index = index
        self.name = name or f'接口{index}'
        self.api_base = api_base
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.proxy_url = proxy_url
        self.proxy_enabled = proxy_enabled
        self.gateway = LLMGateway()

    def gateway_config(self) -> dict:
        """LLMGateway.chat_completions 需要的配置字典。"""
        return {
            **_GATEWAY_DEFAULTS,
            'api_base': self.api_base,
            'api_key': self.api_key,
            'model': self.model,
            'timeout': self.timeout,
            'proxy_url': self.proxy_url,
            'proxy_enabled': self.proxy_enabled,
        }

    def is_usable(self) -> bool:
        """判断接口必填项是否完整，且启用代理时已配置代理地址。"""
        return bool(
            self.api_base
            and self.api_key
            and self.model
            and (not self.proxy_enabled or self.proxy_url)
        )

    def is_healthy(self) -> bool:
        """当前没有处于熔断打开状态即视为健康。"""
        return self.gateway.snapshot().get('state') != 'open'

    def snapshot(self) -> dict:
        """合并接口基本信息与网关运行指标，供管理面板展示。"""
        gate = self.gateway.snapshot()
        return {
            'index': self.index,
            'name': self.name,
            'model': self.model,
            'apiBase': self.api_base,
            'proxyEnabled': self.proxy_enabled,
            'circuit': gate.get('state'),
            'retryAfterSeconds': gate.get('retryAfterSeconds'),
            'requests': gate.get('requests'),
            'successes': gate.get('successes'),
            'failures': gate.get('failures'),
            'lastError': gate.get('lastError'),
            'lastSuccessAt': gate.get('lastSuccessAt'),
            'lastFailureAt': gate.get('lastFailureAt'),
        }


class LLMManager:
    """维护启用的接口列表，并按策略完成调度、故障转移和批量测活。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._providers: list[LLMProvider] = []
        self._strategy = llm_env_store.DEFAULT_STRATEGY
        self._job_filter = False
        self._round_robin = itertools.count()
        self.reload()

    # ------------------------------------------------------------------ 配置

    def reload(self) -> None:
        """Reload providers, retaining gateways whose request configuration is unchanged."""
        config = llm_env_store.load_llm_config()
        with self._lock:
            existing_by_index = {provider.index: provider for provider in self._providers}
        providers = []
        for item in config['providers']:
            if not item.get('enabled'):
                continue
            provider = existing_by_index.get(item['index'])
            unchanged = provider is not None and (
                provider.api_base,
                provider.api_key,
                provider.model,
                provider.timeout,
                provider.proxy_url,
                provider.proxy_enabled,
            ) == (
                item['api_base'],
                item['api_key'],
                item['model'],
                config['timeout'],
                item['proxy_url'],
                item['proxy_enabled'],
            )
            if unchanged:
                provider.name = item['name'] or f'接口{item["index"]}'
            else:
                provider = LLMProvider(
                    index=item['index'],
                    name=item['name'],
                    api_base=item['api_base'],
                    api_key=item['api_key'],
                    model=item['model'],
                    timeout=config['timeout'],
                    proxy_url=item['proxy_url'],
                    proxy_enabled=item['proxy_enabled'],
                )
            if provider.is_usable():
                providers.append(provider)
        with self._lock:
            self._providers = providers
            self._strategy = config['strategy']
            self._job_filter = config['jobFilter']
            self._round_robin = itertools.count()

    @property
    def job_filter_enabled(self) -> bool:
        """线程安全地返回当前是否启用 AI 职位二次筛选。"""
        with self._lock:
            return self._job_filter

    def available(self) -> bool:
        """返回当前是否至少有一个配置完整且启用的接口。"""
        with self._lock:
            return bool(self._providers)

    # ------------------------------------------------------------------ 调度

    def _ordered_candidates(self) -> list[LLMProvider]:
        """按当前策略给出本次请求的接口尝试顺序。"""
        with self._lock:
            providers = list(self._providers)
            strategy = self._strategy
            start = next(self._round_robin) if strategy == 'round_robin' else 0
        if not providers:
            return []
        if strategy == 'round_robin':
            offset = start % len(providers)
            providers = providers[offset:] + providers[:offset]
        # 无论哪种策略，都把当前健康（未熔断）的接口排在前面，熔断的垫后兜底。
        healthy = [p for p in providers if p.is_healthy()]
        unhealthy = [p for p in providers if not p.is_healthy()]
        return healthy + unhealthy

    async def chat_completions(self, payload: dict, purpose: str) -> dict:
        """在候选接口上依次尝试，直到成功；全部失败则抛出最后一个错误。

        payload 不应包含 model，由所选 provider 注入自身模型名。
        """
        candidates = self._ordered_candidates()
        if not candidates:
            raise LLMGatewayError('没有可用的大模型接口，请在网页面板配置并启用')
        last_error: Exception | None = None
        for provider in candidates:
            request_payload = {**payload, 'model': provider.model}
            try:
                return await provider.gateway.chat_completions(
                    provider.gateway_config(), request_payload, purpose
                )
            except LLMGatewayError as error:
                last_error = error
                continue
        raise last_error or LLMGatewayError('全部大模型接口调用失败')

    # ------------------------------------------------------------------ 测活

    async def test_provider(self, index: int) -> dict:
        """向指定接口发送一次最小请求，返回可用性与延迟。"""
        config = llm_env_store.load_llm_config()
        target = next((item for item in config['providers'] if item['index'] == index), None)
        if target is None:
            return {'ok': False, 'error': '接口不存在'}
        return await self._test_provider_config(target, config['timeout'])

    async def test_provider_payload(self, payload: dict) -> dict:
        """使用前端当前卡片内容测活，无需先保存。"""
        try:
            target = llm_env_store.resolve_provider_config(payload)
        except ValueError as error:
            return {'ok': False, 'error': str(error)}
        config = llm_env_store.load_llm_config()
        return await self._test_provider_config(target, config['timeout'])

    @staticmethod
    async def _test_provider_config(target: dict, timeout: int) -> dict:
        """用最小聊天请求测试一份已解析配置，并返回延迟和连接方式。"""
        if not (target['api_base'] and target['api_key'] and target['model']):
            return {'ok': False, 'error': '接口地址、模型或 API Key 未配置完整'}
        if target.get('proxy_enabled') and not target.get('proxy_url'):
            return {'ok': False, 'error': '已启用代理，但代理地址为空'}

        url = f"{target['api_base'].rstrip('/')}/chat/completions"
        headers = {
            'Authorization': f"Bearer {target['api_key']}",
            'Content-Type': 'application/json',
        }
        body = {
            'model': target['model'],
            'messages': [{'role': 'user', 'content': 'ping'}],
            'max_tokens': 1,
            'temperature': 0,
        }
        started = time.monotonic()
        # 与正式请求保持一致：开关关闭时传 None，且禁用环境代理，确保真正直连。
        proxy = target.get('proxy_url') if target.get('proxy_enabled') else None
        try:
            async with httpx.AsyncClient(timeout=min(30, timeout), proxy=proxy, trust_env=False) as client:
                response = await client.post(url, json=body, headers=headers)
            latency = round((time.monotonic() - started) * 1000)
            if response.status_code >= 400:
                detail = response.text[:200]
                return {
                    'ok': False,
                    'status': response.status_code,
                    'latencyMs': latency,
                    'error': f'HTTP {response.status_code}: {detail}',
                }
            data = response.json()
            model = ''
            if isinstance(data, dict):
                model = str(data.get('model') or target['model'])
            return {
                'ok': True,
                'status': response.status_code,
                'latencyMs': latency,
                'model': model,
                'viaProxy': bool(proxy),
            }
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, ImportError) as error:
            latency = round((time.monotonic() - started) * 1000)
            return {'ok': False, 'latencyMs': latency, 'error': f'{type(error).__name__}: {error}'}

    async def test_all(self) -> list[dict]:
        """并发测试所有已保存接口，单个异常不会中断其余结果收集。"""
        config = llm_env_store.load_llm_config()
        results = await asyncio.gather(
            *(self.test_provider(item['index']) for item in config['providers']),
            return_exceptions=True,
        )
        output = []
        for item, result in zip(config['providers'], results):
            entry = {'index': item['index'], 'name': item['name']}
            if isinstance(result, Exception):
                entry.update({'ok': False, 'error': str(result)})
            else:
                entry.update(result)
            output.append(entry)
        return output

    # ------------------------------------------------------------------ 快照

    def reset_circuits(self, clear_cache: bool = False) -> None:
        """重置所有 provider 的熔断状态，可选清除各自缓存。"""
        with self._lock:
            providers = list(self._providers)
        for provider in providers:
            provider.gateway.reset_circuit(clear_cache=clear_cache)

    def snapshot(self) -> dict:
        """汇总管理器配置和每个接口的网关指标，供面板展示。"""
        with self._lock:
            providers = list(self._providers)
            strategy = self._strategy
            job_filter = self._job_filter
        return {
            'enabled': bool(providers),
            'strategy': strategy,
            'jobFilter': job_filter,
            'providerCount': len(providers),
            'providers': [provider.snapshot() for provider in providers],
        }


LLM_MANAGER = LLMManager()
