from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.llm.gateway import LLMGateway, LLMGatewayError
from app.llm.manager import LLM_MANAGER
from app.llm.tasks import strict_llm_job_filter


def response(content: str) -> dict:
    return {
        'choices': [{
            'message': {'content': content},
            'finish_reason': 'stop',
        }],
    }


class StrictJobFilterTests(unittest.TestCase):
    def run_filter(self):
        return asyncio.run(strict_llm_job_filter('SRE', '20K', 'Linux'))

    def enabled_patches(self):
        return (
            patch.object(type(LLM_MANAGER), 'job_filter_enabled', new_callable=lambda: property(lambda _self: True)),
            patch.object(LLM_MANAGER, 'available', return_value=True),
            patch('app.llm.tasks.load_resume', return_value='resume'),
        )

    def test_reliable_pass_or_reject_is_reported_as_reliable(self) -> None:
        enabled, available, resume = self.enabled_patches()
        with enabled, available, resume, patch.object(
            LLM_MANAGER, 'chat_completions', new=AsyncMock(return_value=response('false\n经验不匹配')),
        ):
            passed, reason, reliable = self.run_filter()
        self.assertFalse(passed)
        self.assertEqual(reason, '经验不匹配')
        self.assertTrue(reliable)

    def test_two_malformed_responses_fail_closed_and_are_unreliable(self) -> None:
        enabled, available, resume = self.enabled_patches()
        completion = AsyncMock(side_effect=[response('maybe'), response('still maybe')])
        with enabled, available, resume, patch.object(
            LLM_MANAGER, 'chat_completions', new=completion,
        ):
            passed, reason, reliable = self.run_filter()
        self.assertFalse(passed)
        self.assertEqual(reason, 'ai_unreliable_format')
        self.assertFalse(reliable)
        self.assertEqual(completion.await_count, 2)

    def test_truncated_verdict_is_corrected_before_it_can_be_cached(self) -> None:
        enabled, available, resume = self.enabled_patches()
        truncated = response('true\n技能匹配')
        truncated['choices'][0]['finish_reason'] = 'length'
        completion = AsyncMock(side_effect=[
            truncated,
            response('false\n响应截断，结论不可靠'),
        ])
        with enabled, available, resume, patch.object(
            LLM_MANAGER, 'chat_completions', new=completion,
        ):
            passed, reason, reliable = self.run_filter()

        self.assertFalse(passed)
        self.assertEqual(reason, '响应截断，结论不可靠')
        self.assertTrue(reliable)
        self.assertEqual(completion.await_count, 2)

    def test_verdict_without_reason_is_corrected_before_it_is_reliable(self) -> None:
        enabled, available, resume = self.enabled_patches()
        completion = AsyncMock(side_effect=[
            response('true'),
            response('true\n技能与岗位要求匹配'),
        ])
        with enabled, available, resume, patch.object(
            LLM_MANAGER, 'chat_completions', new=completion,
        ):
            passed, reason, reliable = self.run_filter()

        self.assertTrue(passed)
        self.assertEqual(reason, '技能与岗位要求匹配')
        self.assertTrue(reliable)
        self.assertEqual(completion.await_count, 2)

    def test_transport_failure_fails_closed_without_domain_cache_signal(self) -> None:
        enabled, available, resume = self.enabled_patches()
        with enabled, available, resume, patch.object(
            LLM_MANAGER,
            'chat_completions',
            new=AsyncMock(side_effect=LLMGatewayError('network down')),
        ):
            passed, reason, reliable = self.run_filter()
        self.assertFalse(passed)
        self.assertIn('network down', reason)
        self.assertFalse(reliable)

    def test_enabled_filter_with_no_available_provider_fails_closed(self) -> None:
        enabled, _available, resume = self.enabled_patches()
        completion = AsyncMock()
        with (
            enabled,
            patch.object(LLM_MANAGER, 'available', return_value=False),
            resume,
            patch.object(LLM_MANAGER, 'chat_completions', new=completion),
        ):
            passed, reason, reliable = self.run_filter()

        self.assertFalse(passed)
        self.assertEqual(reason, 'ai_unavailable')
        self.assertFalse(reliable)
        completion.assert_not_awaited()

    def test_disabled_filter_skip_is_never_a_cacheable_ai_decision(self) -> None:
        with patch.object(
            type(LLM_MANAGER),
            'job_filter_enabled',
            new_callable=lambda: property(lambda _self: False),
        ):
            passed, reason, reliable = self.run_filter()

        self.assertTrue(passed)
        self.assertEqual(reason, '未启用AI筛选')
        self.assertFalse(reliable)


class JobFilterGatewayCacheTests(unittest.TestCase):
    def test_job_filter_raw_responses_are_not_cached(self) -> None:
        async def exercise() -> int:
            gateway = LLMGateway()
            gateway._request = AsyncMock(return_value=response('malformed but nonempty'))
            config = {'api_base': 'https://example.test', 'model': 'm1', 'cache_ttl_seconds': 1800}
            payload = {'messages': [{'role': 'user', 'content': 'same'}]}
            await gateway.chat_completions(config, payload, 'job_filter')
            await asyncio.sleep(0)
            await gateway.chat_completions(config, payload, 'job_filter')
            await asyncio.sleep(0)
            return gateway._request.await_count

        self.assertEqual(asyncio.run(exercise()), 2)


if __name__ == '__main__':
    unittest.main()
