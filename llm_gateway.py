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


def _response_text(value) -> str:
    """Normalize text returned by OpenAI-compatible providers."""
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
    def __init__(self, message: str, *, status_code: int | None = None, circuit_open: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.circuit_open = circuit_open


class LLMGateway:
    """Process-wide LLM concurrency gate with retry, cache and circuit breaker."""

    def __init__(self, client_factory: Callable | None = None):
        self._client_factory = client_factory or (
            lambda timeout, proxy: httpx.AsyncClient(timeout=timeout, proxy=proxy)
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
        if ttl <= 0:
            return
        with self._lock:
            self._cache[key] = (time.monotonic() + ttl, value)
            self._cache.move_to_end(key)
            while len(self._cache) > 512:
                self._cache.popitem(last=False)

    def _check_circuit(self) -> None:
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
                if self._half_open_in_progress:
                    self._circuit_rejections += 1
                    raise LLMGatewayError('LLM circuit half-open probe in progress', circuit_open=True)
                self._half_open_in_progress = True

    def _record_success(self) -> None:
        with self._lock:
            self._successes += 1
            self._consecutive_failures = 0
            self._open_until = 0.0
            self._half_open_in_progress = False
            self._last_error = ''
            self._last_success_at = datetime.now().isoformat(timespec='seconds')

    def _record_failure(self, config: dict, error: Exception) -> None:
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
        if interval <= 0:
            return 0.0
        now = time.monotonic()
        with self._lock:
            scheduled = max(now, self._last_request_slot + interval)
            self._last_request_slot = scheduled
        return max(0.0, scheduled - now)

    @staticmethod
    def _retry_after(response: httpx.Response | None) -> float:
        if response is None:
            return 0.0
        value = response.headers.get('Retry-After', '').strip()
        try:
            return max(0.0, float(value))
        except ValueError:
            return 0.0

    async def _request(self, config: dict, payload: dict) -> dict:
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
            self._check_circuit()
            with self._lock:
                self._requests += 1
            last_error: Exception | None = None
            async with self._client_factory(timeout, proxy) as client:
                for attempt in range(retries + 1):
                    wait_for_slot = self._reserve_request_slot(min_interval)
                    if wait_for_slot:
                        await asyncio.sleep(wait_for_slot)
                    response = None
                    try:
                        response = await client.post(url, json=payload, headers=headers)
                        response.raise_for_status()
                        data = response.json()
                        if not isinstance(data, dict):
                            raise ValueError('LLM response must be a JSON object')
                        choices = data.get('choices')
                        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                            raise ValueError('LLM response is missing choices')
                        message = choices[0].get('message')
                        if not isinstance(message, dict):
                            raise ValueError('LLM response is missing message')
                        if (
                            not _response_text(message.get('content'))
                            and not _response_text(message.get('reasoning_content'))
                            and not _response_text(message.get('reasoning'))
                        ):
                            raise ValueError('LLM response content is empty')
                        self._record_success()
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
                        delay = min(max_delay, base_delay * (2 ** attempt))
                        delay = max(retry_after, delay + random.uniform(0, max(0.05, delay * 0.2)))
                        await asyncio.sleep(delay)

            final_error = last_error or LLMGatewayError('unknown LLM gateway error')
            self._record_failure(config, final_error)
            raise final_error

    async def chat_completions(self, config: dict, payload: dict, purpose: str) -> dict:
        cache_ttl = self._float(config, 'cache_ttl_seconds', 1800)
        cache_key = self._cache_key(config, payload, purpose)
        cached = self._get_cached(cache_key, cache_ttl)
        if cached is not None:
            return cached

        loop_key = id(asyncio.get_running_loop())
        inflight_key = (loop_key, cache_key)
        with self._lock:
            task = self._inflight.get(inflight_key)
            owner = task is None
            if owner:
                task = asyncio.create_task(self._request(config, payload))
                self._inflight[inflight_key] = task
        try:
            result = await asyncio.shield(task)
            choices = result.get('choices') if isinstance(result, dict) else None
            finish_reason = choices[0].get('finish_reason') if choices and isinstance(choices[0], dict) else None
            message = choices[0].get('message') if choices and isinstance(choices[0], dict) else None
            has_final_content = isinstance(message, dict) and bool(_response_text(message.get('content')))
            # Reasoning-only responses are valid upstream responses and must not
            # trip the circuit, but they are not reusable final answers.
            if owner and finish_reason != 'length' and has_final_content:
                self._put_cached(cache_key, result, cache_ttl)
            return result
        finally:
            if owner:
                with self._lock:
                    self._inflight.pop(inflight_key, None)

    def reset_circuit(self, clear_cache: bool = False) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._open_until = 0.0
            self._half_open_in_progress = False
            self._last_error = ''
            if clear_cache:
                self._cache.clear()

    def snapshot(self) -> dict:
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
