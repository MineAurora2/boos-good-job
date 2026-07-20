from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.llm import tasks
from app.llm.gateway import LLMGatewayError


def _completion(
    content: str | None,
    *,
    reasoning: str | None = None,
    finish_reason: str = 'stop',
) -> dict:
    message: dict[str, object] = {'content': content}
    if reasoning is not None:
        message['reasoning_content'] = reasoning
    return {
        'choices': [
            {
                'message': message,
                'finish_reason': finish_reason,
            }
        ]
    }


class JobFilterResponseParserTests(unittest.TestCase):
    def assertParsed(
        self,
        content: str,
        expected_passed: bool | None,
        expected_reason: str,
    ) -> None:
        passed, reason = tasks._parse_job_filter_response(content)
        self.assertIs(passed, expected_passed)
        self.assertEqual(reason, expected_reason)

    def test_parses_bare_english_decisions(self) -> None:
        self.assertParsed('true\n技能和岗位要求匹配', True, '技能和岗位要求匹配')
        self.assertParsed('false\n工作内容与简历不匹配', False, '工作内容与简历不匹配')

    def test_parses_chinese_decisions(self) -> None:
        self.assertParsed('通过\n经验符合要求', True, '经验符合要求')
        self.assertParsed('不通过\n岗位方向不匹配', False, '岗位方向不匹配')

    def test_parses_numbered_and_prefixed_decisions(self) -> None:
        self.assertParsed('1. true\n技术栈匹配', True, '技术栈匹配')
        self.assertParsed('结论：通过\n原因：具备相关经验', True, '具备相关经验')
        self.assertParsed('判断: false\n原因: 行业方向不符', False, '行业方向不符')

    def test_parses_markdown_wrapped_decision(self) -> None:
        self.assertParsed(
            '```text\n**true**\n技能符合岗位要求\n```',
            True,
            '技能符合岗位要求',
        )
        self.assertParsed('*不通过*\n岗位方向不符', False, '岗位方向不符')
        self.assertParsed('(1) true\n具备相关技能', True, '具备相关技能')
        self.assertParsed('**result**: **true**\n技能符合岗位要求', True, '技能符合岗位要求')

    def test_parses_json_boolean_and_reason(self) -> None:
        self.assertParsed(
            json.dumps(
                {'passed': True, 'reason': '**技能匹配**'},
                ensure_ascii=False,
            ),
            True,
            '技能匹配',
        )
        self.assertParsed(
            '{"passed": false, "reason": "缺少相关经验"}',
            False,
            '缺少相关经验',
        )
        self.assertParsed(
            '{"passed": false, "reason": "缺少相关经验", "confidence": 0.9}',
            False,
            '缺少相关经验',
        )

    def test_rejects_json_string_decision(self) -> None:
        self.assertParsed(
            '{"passed": "false", "reason": "字符串不是明确布尔结论"}',
            None,
            '',
        )

    def test_missing_reason_uses_stable_fallback(self) -> None:
        self.assertParsed('true', True, 'AI未提供理由')

    def test_reason_is_cleaned_and_limited_to_200_characters(self) -> None:
        passed, reason = tasks._parse_job_filter_response(
            '结论：通过\n原因：**' + ('匹配' * 150) + '**'
        )
        self.assertIs(passed, True)
        self.assertNotIn('*', reason)
        self.assertLessEqual(len(reason), 200)

    def test_reason_sentences_that_mention_false_are_not_decisions(self) -> None:
        for content in (
            '未触发 false 条件，因此岗位要求与简历较为匹配。',
            '根据要求，不返回false，岗位属于计算机相关方向。',
        ):
            with self.subTest(content=content):
                self.assertParsed(content, None, '')

    def test_reason_examples_after_a_decision_do_not_override_it(self) -> None:
        self.assertParsed(
            'true\n岗位与简历匹配。\n不符合时应返回：\n“false”',
            True,
            '岗位与简历匹配。 不符合时应返回：',
        )
        self.assertParsed(
            '岗位与简历匹配。\n不符合时应返回：\n“false”',
            None,
            '',
        )
        self.assertParsed(
            '岗位分析尚未完成。\n结论：通过\n理由：技能匹配',
            True,
            '技能匹配',
        )
        self.assertParsed('岗位分析完成。\nfalse', False, 'AI未提供理由')

    def test_conflicting_or_nonstandard_content_is_indeterminate(self) -> None:
        for content in (
            '结论：true\n结论：false\n模型给出了冲突结论',
            'true\n理由尚未完成\nfalse',
            '岗位方向和候选人的经历较为匹配。',
            'truthy\n看起来大致符合',
            '',
        ):
            with self.subTest(content=content):
                self.assertParsed(content, None, '')


class JobFilterRequestTests(unittest.IsolatedAsyncioTestCase):
    def _manager(self, *responses: dict) -> Mock:
        manager = Mock()
        manager.job_filter_enabled = True
        manager.available.return_value = True
        manager.chat_completions = AsyncMock(side_effect=list(responses))
        return manager

    async def _run_filter(self, manager: Mock) -> tuple[bool, str]:
        with (
            patch.object(tasks, 'LLM_MANAGER', manager),
            patch.object(tasks, 'load_resume', return_value='Python 自动化开发经历'),
        ):
            return await tasks.llm_job_filter(
                'Python 开发工程师',
                '15-25K',
                '负责 Python 自动化平台开发',
            )

    async def test_clear_first_response_does_not_retry(self) -> None:
        manager = self._manager(_completion('true\n技能匹配'))

        result = await self._run_filter(manager)

        self.assertEqual(result, (True, '技能匹配'))
        manager.chat_completions.assert_awaited_once()
        payload, purpose = manager.chat_completions.await_args.args
        self.assertEqual(purpose, 'job_filter')
        self.assertEqual(payload['max_tokens'], 512)

    async def test_reasoning_only_response_is_retried_without_reusing_reasoning(self) -> None:
        leaked_reasoning = 'SECRET_REASONING_SHOULD_NOT_BE_REUSED'
        manager = self._manager(
            _completion('', reasoning=f'true\n{leaked_reasoning}'),
            _completion('true\n技能和岗位要求匹配'),
        )

        result = await self._run_filter(manager)

        self.assertEqual(result, (True, '技能和岗位要求匹配'))
        self.assertEqual(manager.chat_completions.await_count, 2)
        first_call, retry_call = manager.chat_completions.await_args_list
        first_payload, first_purpose = first_call.args
        retry_payload, retry_purpose = retry_call.args
        self.assertEqual(first_purpose, 'job_filter')
        self.assertEqual(retry_purpose, 'job_filter_retry')
        self.assertEqual(first_payload['max_tokens'], 512)
        self.assertEqual(retry_payload['max_tokens'], 1024)
        self.assertEqual(retry_payload['temperature'], 0)
        self.assertNotIn(leaked_reasoning, json.dumps(retry_payload, ensure_ascii=False))
        retry_text = json.dumps(retry_payload['messages'], ensure_ascii=False)
        self.assertIn('Python 自动化开发经历', retry_text)
        self.assertIn('Python 开发工程师', retry_text)

    async def test_gateway_empty_response_enters_task_retry(self) -> None:
        manager = self._manager(
            LLMGatewayError('ValueError: LLM response content is empty'),
            _completion('true\n网关空响应后重试成功'),
        )

        result = await self._run_filter(manager)

        self.assertEqual(result, (True, '网关空响应后重试成功'))
        self.assertEqual(manager.chat_completions.await_count, 2)

    async def test_second_gateway_empty_response_fails_closed(self) -> None:
        manager = self._manager(
            LLMGatewayError('ValueError: LLM response content is empty'),
            LLMGatewayError('ValueError: LLM response content is empty'),
        )

        result = await self._run_filter(manager)

        self.assertEqual(result, (False, 'ai_unreliable_format'))
        self.assertEqual(manager.chat_completions.await_count, 2)

    async def test_empty_response_is_retried(self) -> None:
        manager = self._manager(
            _completion(''),
            _completion('true\n重试后给出明确结论'),
        )

        result = await self._run_filter(manager)

        self.assertEqual(result, (True, '重试后给出明确结论'))
        self.assertEqual(manager.chat_completions.await_count, 2)

    async def test_truncated_response_without_verdict_is_retried(self) -> None:
        manager = self._manager(
            _completion('正在分析岗位要求', finish_reason='length'),
            _completion('false\n岗位明确要求不具备的经验'),
        )

        result = await self._run_filter(manager)

        self.assertEqual(result, (False, '岗位明确要求不具备的经验'))
        self.assertEqual(manager.chat_completions.await_count, 2)

    async def test_invalid_first_response_retries_once(self) -> None:
        manager = self._manager(
            _completion('岗位和候选人经历比较匹配。'),
            _completion('false\n岗位方向不匹配'),
        )

        result = await self._run_filter(manager)

        self.assertEqual(result, (False, '岗位方向不匹配'))
        self.assertEqual(
            [call.args[1] for call in manager.chat_completions.await_args_list],
            ['job_filter', 'job_filter_retry'],
        )

    async def test_two_indeterminate_responses_fail_closed(self) -> None:
        manager = self._manager(
            _completion('未触发 false 条件，因此符合。'),
            _completion('', reasoning='仍在分析，没有最终答案', finish_reason='length'),
        )

        result = await self._run_filter(manager)

        self.assertEqual(result, (False, 'ai_unreliable_format'))
        self.assertEqual(manager.chat_completions.await_count, 2)

    async def test_explicit_false_does_not_retry(self) -> None:
        manager = self._manager(_completion('不通过\n经验不符合岗位要求'))

        result = await self._run_filter(manager)

        self.assertEqual(result, (False, '经验不符合岗位要求'))
        manager.chat_completions.assert_awaited_once()
        self.assertEqual(manager.chat_completions.await_args.args[1], 'job_filter')


if __name__ == '__main__':
    unittest.main()
