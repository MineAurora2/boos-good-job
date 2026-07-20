"""OpenAI 兼容接口的底层请求网关。

该模块集中处理单个大模型接口的并发限制、请求节流、失败重试、结果缓存、
相同请求合并和熔断状态。上层的多接口选择与故障转移由 ``llm_manager`` 负责。
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime
import hashlib
import json
import random
import threading
import time
from typing import Callable

import httpx


RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


def _valid_token(value: object) -> int | None:
    """Accept only native, non-negative integer token counts."""
    if type(value) is int and value >= 0:
        return value
    return None


def extract_usage(data: object) -> tuple[bool, int, int, int]:
    """Extract token usage from either OpenAI or input/output naming schemes."""
    if not isinstance(data, dict):
        return False, 0, 0, 0
    usage = data.get('usage')
    if not isinstance(usage, dict):
        return False, 0, 0, 0

    def first_valid(*keys: str) -> int | None:
        for key in keys:
            value = _valid_token(usage.get(key))
            if value is not None:
                return value
        return None

    input_tokens = first_valid('input_tokens', 'prompt_tokens')
    output_tokens = first_valid('output_tokens', 'completion_tokens')
    total_tokens = first_valid('total_tokens')
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    reported = any(value is not None for value in (input_tokens, output_tokens, total_tokens))
    return (
        reported,
        input_tokens or 0,
        output_tokens or 0,
        total_tokens or 0,
    )


# Keep a private spelling available to callers that used the helper during rollout.
_extract_usage = extract_usage


def response_text(value) -> str:
    """把不同兼容服务返回的字符串或内容分片统一整理为纯文本。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get('text') or item.get('content')
                if isinstance(text, str):
                    parts.append(text)
        return '\n'.join(part.strip() for part in parts if part.strip()).strip()
    return ''


class LLMGatewayError(RuntimeError):
    """包装请求失败，并携带 HTTP 状态码和熔断拒绝标记。"""

    def __init__(self, message: str, *, status_code: int | None = None, circuit_open: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.circuit_open = circuit_open


class LLMGateway:
    """单个接口的进程级并发网关，提供重试、缓存和熔断保护。"""

    def __init__(self, client_factory: Callable | None = None):
        # trust_env=False 很重要：只有显式启用的 provider 代理才会生效，避免系统环境
        # 中的 HTTP_PROXY/HTTPS_PROXY 悄悄改变“关闭代理”接口的连接路径。
        self._client_factory = client_factory or (
            lambda timeout, proxy: httpx.AsyncClient(timeout=timeout, proxy=proxy, trust_env=False)
        )
        self._lock = threading.Lock()
        self._semaphores: dict[tuple[int, int], asyncio.Semaphore] = {}
        self._inflight: dict[tuple[int, str], asyncio.Task] = {}
        self._cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._last_request_slot = 0.0
        self._consecutive_failures = 0
        self._open_until = 0.0
        self._half_open_in_progress = False
        self._last_error = ''
        self._last_success_at = ''
        self._last_failure_at = ''
        self._requests = 0
        self._successes = 0
        self._failures = 0
        self._retries = 0
        self._cache_hits = 0
        self._circuit_rejections = 0

    @staticmethod
    def _int(config: dict, key: str, default: int, minimum: int = 0) -> int:
        try:
            return max(minimum, int(config.get(key, default)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _float(config: dict, key: str, default: float, minimum: float = 0.0) -> float:
        try:
            return max(minimum, float(config.get(key, default)))
        except (TypeError, ValueError):
            return default

    def _semaphore(self, limit: int) -> asyncio.Semaphore:
        """按事件循环和并发上限复用信号量，避免跨事件循环使用异步原语。"""
        loop_key = id(asyncio.get_running_loop())
        key = (loop_key, limit)
        with self._lock:
            semaphore = self._semaphores.get(key)
            if semaphore is None:
                semaphore = asyncio.Semaphore(limit)
                self._semaphores[key] = semaphore
            return semaphore

    @staticmethod
    def _cache_key(config: dict, payload: dict, purpose: str) -> str:
        """根据接口、模型、业务用途和请求体生成稳定的缓存键。"""
        source = json.dumps(
            {
                'purpose': purpose,
                'api_base': config.get('api_base'),
                'model': config.get('model'),
                'payload': payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        return hashlib.sha256(source.encode('utf-8')).hexdigest()

    def _get_cached(self, key: str, ttl: float) -> dict | None:
        """读取未过期缓存，并更新 LRU 顺序和命中计数。"""
        if ttl <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if not cached:
                return None
            expires_at, value = cached
            if expires_at <= now:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            self._cache_hits += 1
            return value

    def _put_cached(self, key: str, value: dict, ttl: float) -> None:
        """写入有过期时间的结果；最多保留 512 条，防止常驻进程无限增长。"""
        if ttl <= 0:
            return
        with self._lock:
            self._cache[key] = (time.monotonic() + ttl, value)
            self._cache.move_to_end(key)
            while len(self._cache) > 512:
                self._cache.popitem(last=False)

    def _check_circuit(self) -> bool:
        """检查熔断状态，并在冷却结束后只放行一个半开探测请求。

        返回本次调用是否成为半开探测：为 True 时调用方必须保证最终清除
        ``_half_open_in_progress``，否则探测意外中断后熔断器会永久卡在半开拒绝态。
        """
        now = time.monotonic()
        with self._lock:
            if self._open_until > now:
                self._circuit_rejections += 1
                remaining = max(1, int(self._open_until - now + 0.999))
                raise LLMGatewayError(
                    f'LLM circuit open, retry after {remaining}s',
                    circuit_open=True,
                )
            if self._open_until:
                # 冷却期结束后进入半开状态。并发请求中只有第一个负责探测上游，
                # 其余请求立即失败，避免故障服务刚恢复时被瞬时流量再次压垮。
                if self._half_open_in_progress:
                    self._circuit_rejections += 1
                    raise LLMGatewayError('LLM circuit half-open probe in progress', circuit_open=True)
                self._half_open_in_progress = True
                return True
        return False

    def _clear_half_open_probe(self) -> None:
        """兜底清除半开探测标记，供探测请求因意外异常（如取消）跳过成败记录时使用。"""
        with self._lock:
            self._half_open_in_progress = False

    def _record_success(self) -> None:
        """记录成功并关闭熔断器；半开探测成功也在这里恢复正常流量。"""
        with self._lock:
            self._successes += 1
            self._consecutive_failures = 0
            self._open_until = 0.0
            self._half_open_in_progress = False
            self._last_error = ''
            self._last_success_at = datetime.now().isoformat(timespec='seconds')

    def _record_failure(self, config: dict, error: Exception) -> None:
        """累计连续失败，达到阈值后在配置的冷却时间内打开熔断器。"""
        threshold = self._int(config, 'circuit_failure_threshold', 3, 1)
        open_seconds = self._float(config, 'circuit_open_seconds', 60, 1)
        with self._lock:
            self._failures += 1
            self._consecutive_failures += 1
            self._half_open_in_progress = False
            self._last_error = str(error)[:500]
            self._last_failure_at = datetime.now().isoformat(timespec='seconds')
            if self._consecutive_failures >= threshold:
                self._open_until = time.monotonic() + open_seconds

    def _reserve_request_slot(self, interval: float) -> float:
        """原子预订下一个发送时刻，使并发任务也遵守最小请求间隔。"""
        if interval <= 0:
            return 0.0
        now = time.monotonic()
        with self._lock:
            scheduled = max(now, self._last_request_slot + interval)
            self._last_request_slot = scheduled
        return max(0.0, scheduled - now)

    @staticmethod
    def _retry_after(response: httpx.Response | None) -> float:
        """解析服务端 Retry-After 秒数；无效值按无需额外等待处理。"""
        if response is None:
            return 0.0
        value = response.headers.get('Retry-After', '').strip()
        try:
            return max(0.0, float(value))
        except ValueError:
            return 0.0

    @staticmethod
    async def _notify_attempt(observer: Callable | None, event: dict) -> None:
        """Run accounting off the event loop without changing request outcomes."""
        if observer is None:
            return
        try:
            result = await asyncio.to_thread(observer, event)
            if hasattr(result, '__await__'):
                await result
        except Exception:
            # Accounting is deliberately fail-open. The manager logs recorder failures.
            return

    async def _request(
        self,
        config: dict,
        payload: dict,
        purpose: str = '',
        attempt_observer: Callable | None = None,
    ) -> dict:
        """执行一次逻辑请求，包含限流、重试、响应校验和熔断统计。"""
        max_concurrent = self._int(config, 'max_concurrent_requests', 1, 1)
        retries = self._int(config, 'retry_count', 2, 0)
        base_delay = self._float(config, 'retry_base_delay', 1.0)
        max_delay = self._float(config, 'retry_max_delay', 8.0)
        min_interval = self._float(config, 'min_request_interval', 0.8)
        timeout = self._float(config, 'timeout', 30, 1)
        proxy = str(config.get('proxy_url') or '').strip() if config.get('proxy_enabled') else None
        url = f"{str(config['api_base']).rstrip('/')}/chat/completions"
        headers = {
            'Authorization': f"Bearer {config['api_key']}",
            'Content-Type': 'application/json',
        }

        async with self._semaphore(max_concurrent):
            is_probe = self._check_circuit()
            with self._lock:
                self._requests += 1
            last_error: Exception | None = None
            outcome_recorded = False
            try:
                try:
                    client_context = self._client_factory(timeout, proxy)
                except (httpx.InvalidURL, ValueError, ImportError) as error:
                    final_error = LLMGatewayError(f'{type(error).__name__}: {error}')
                    self._record_failure(config, final_error)
                    outcome_recorded = True
                    raise final_error from error
                async with client_context as client:
                    for attempt in range(retries + 1):
                        wait_for_slot = self._reserve_request_slot(min_interval)
                        if wait_for_slot:
                            await asyncio.sleep(wait_for_slot)
                        response = None
                        data = None
                        usage_reported = False
                        input_tokens = output_tokens = total_tokens = 0
                        attempt_model = str(config.get('model') or '')
                        attempt_started = False
                        attempt_success = False
                        retry_delay: float | None = None
                        try:
                            # Set this immediately before invoking the transport. A circuit
                            # rejection, client construction error, or throttling cancellation
                            # therefore cannot be mistaken for a real upstream attempt.
                            attempt_started = True
                            response = await client.post(url, json=payload, headers=headers)
                            parse_error = None
                            try:
                                data = response.json()
                            except (ValueError, json.JSONDecodeError) as error:
                                parse_error = error
                            if isinstance(data, dict):
                                usage_reported, input_tokens, output_tokens, total_tokens = extract_usage(data)
                                attempt_model = str(data.get('model') or config.get('model') or '')
                            response.raise_for_status()
                            if parse_error is not None:
                                raise parse_error
                            if not isinstance(data, dict):
                                raise ValueError('LLM response must be a JSON object')
                            choices = data.get('choices')
                            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                                raise ValueError('LLM response is missing choices')
                            message = choices[0].get('message')
                            if not isinstance(message, dict):
                                raise ValueError('LLM response is missing message')
                            if (
                                not response_text(message.get('content'))
                                and not response_text(message.get('reasoning_content'))
                                and not response_text(message.get('reasoning'))
                            ):
                                raise ValueError('LLM response content is empty')
                            self._record_success()
                            outcome_recorded = True
                            attempt_success = True
                            return data
                        except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError) as error:
                            status_code = response.status_code if response is not None else None
                            body = response.text[:300] if response is not None else ''
                            message = f'{type(error).__name__}: {error}'
                            if body:
                                message += f' | body={body}'
                            last_error = LLMGatewayError(message, status_code=status_code)
                            retryable = status_code in RETRYABLE_STATUS_CODES or status_code is None
                            if attempt >= retries or not retryable:
                                break
                            with self._lock:
                                self._retries += 1
                            retry_after = self._retry_after(response)
                            # 指数退避叠加少量随机抖动，减少多个任务同时重试造成的尖峰；
                            # 服务端明确给出的 Retry-After 拥有更高优先级。
                            delay = min(max_delay, base_delay * (2 ** attempt))
                            retry_delay = max(
                                retry_after,
                                delay + random.uniform(0, max(0.05, delay * 0.2)),
                            )
                        finally:
                            if attempt_started:
                                await self._notify_attempt(
                                    attempt_observer,
                                    {
                                        'provider_id': config.get('provider_id', ''),
                                        'provider_name': config.get('provider_name', ''),
                                        'api_base': config.get('api_base', ''),
                                        'model': attempt_model,
                                        'purpose': purpose,
                                        'success': attempt_success,
                                        'usage_reported': usage_reported,
                                        'input_tokens': input_tokens,
                                        'output_tokens': output_tokens,
                                        'total_tokens': total_tokens,
                                        'occurred_at': datetime.now().astimezone(),
                                    },
                                )
                        if retry_delay is not None:
                            await asyncio.sleep(retry_delay)

                final_error = last_error or LLMGatewayError('unknown LLM gateway error')
                self._record_failure(config, final_error)
                outcome_recorded = True
                raise final_error
            finally:
                # 探测请求若因取消等未被捕获的异常跳过了成败记录，必须在此清除半开标记，
                # 否则冷却已过但标记长期为 True，后续所有请求都会被半开分支永久拒绝。
                if is_probe and not outcome_recorded:
                    self._clear_half_open_probe()

    async def chat_completions(
        self,
        config: dict,
        payload: dict,
        purpose: str,
        attempt_observer: Callable | None = None,
    ) -> dict:
        """返回聊天补全结果，并合并同一事件循环中的相同在途请求。"""
        # Job-filter responses are parsed by the domain layer. Caching a
        # syntactically successful but malformed response here would make the
        # format-correction path replay the same bad payload indefinitely.
        cache_ttl = (
            0.0
            if str(purpose or '').startswith('job_filter')
            else self._float(config, 'cache_ttl_seconds', 1800)
        )
        cache_key = self._cache_key(config, payload, purpose)
        cached = self._get_cached(cache_key, cache_ttl)
        if cached is not None:
            return cached

        loop_key = id(asyncio.get_running_loop())
        inflight_key = (loop_key, cache_key)
        with self._lock:
            task = self._inflight.get(inflight_key)
            if task is None:
                task = asyncio.create_task(
                    self._request(config, payload, purpose, attempt_observer)
                )
                self._inflight[inflight_key] = task

                def complete(completed: asyncio.Task) -> None:
                    try:
                        result = completed.result()
                    except (asyncio.CancelledError, Exception):
                        pass
                    else:
                        choices = result.get('choices') if isinstance(result, dict) else None
                        finish_reason = (
                            choices[0].get('finish_reason')
                            if choices and isinstance(choices[0], dict)
                            else None
                        )
                        message = (
                            choices[0].get('message')
                            if choices and isinstance(choices[0], dict)
                            else None
                        )
                        has_final_content = isinstance(message, dict) and bool(
                            response_text(message.get('content'))
                        )
                        if finish_reason != 'length' and has_final_content:
                            self._put_cached(cache_key, result, cache_ttl)
                    finally:
                        with self._lock:
                            if self._inflight.get(inflight_key) is completed:
                                self._inflight.pop(inflight_key, None)

                # Cleanup belongs to the network task itself. A cancelled caller only stops
                # waiting; it must not make an active paid request look absent to later callers.
                task.add_done_callback(complete)

        result = await asyncio.shield(task)
        return result

    def reset_circuit(self, clear_cache: bool = False) -> None:
        """手动重置熔断状态，并可选择同时清空结果缓存。"""
        with self._lock:
            self._consecutive_failures = 0
            self._open_until = 0.0
            self._half_open_in_progress = False
            self._last_error = ''
            if clear_cache:
                self._cache.clear()

    def snapshot(self) -> dict:
        """返回供管理面板展示的只读运行状态和累计指标。"""
        now = time.monotonic()
        with self._lock:
            remaining = max(0, int(self._open_until - now + 0.999))
            state = 'open' if remaining else ('half_open' if self._half_open_in_progress else 'closed')
            return {
                'state': state,
                'retryAfterSeconds': remaining,
                'consecutiveFailures': self._consecutive_failures,
                'lastError': self._last_error,
                'lastSuccessAt': self._last_success_at,
                'lastFailureAt': self._last_failure_at,
                'requests': self._requests,
                'successes': self._successes,
                'failures': self._failures,
                'retries': self._retries,
                'cacheHits': self._cache_hits,
                'circuitRejections': self._circuit_rejections,
                'cacheSize': len(self._cache),
                'inflight': len(self._inflight),
            }
