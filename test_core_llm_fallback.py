import asyncio
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import AsyncMock, patch

import core
from llm_gateway import LLMGatewayError


class CoreLLMFallbackTest(unittest.TestCase):
    def test_score_only_delivery_does_not_generate_greeting(self):
        mocked = AsyncMock(return_value='should not be used')
        with (
            patch.object(core.Config, 'title_deduction_keywords', {}),
            patch.object(core.Config, 'detail_deduction_keywords', {}),
            patch.object(core, 'generateCustomIntroduce', mocked),
        ):
            result = asyncio.run(core.evaluateSingleRouteDelivery('# 职位名称\nSRE\n\n# 职位描述\n运维'))

        mocked.assert_not_awaited()
        self.assertEqual(result['introduce'], core.Config.introduce)

    def test_length_limited_reasoning_response_retries_for_final_greeting(self):
        llm_config = {
            'enabled': True, 'api_base': 'https://llm.example/v1', 'api_key': 'test-key',
            'model': 'reasoning-model', 'introduce_max_tokens': 256,
            'introduce_retry_max_tokens': 2048, 'reasoning_effort': 'low',
        }
        responses = [
            {'choices': [{'finish_reason': 'length', 'message': {'content': None, 'reasoning': '仍在分析'}}]},
            {'choices': [{'finish_reason': 'stop', 'message': {'content': '您好，我对这个岗位很感兴趣，方便进一步沟通吗？'}}]},
        ]
        mocked = AsyncMock(side_effect=responses)
        with (
            patch.object(core.Config, 'llm', llm_config),
            patch.object(core.Config, 'resume_content', '测试简历'),
            patch.object(core.LLM_GATEWAY, 'chat_completions', mocked),
        ):
            result = asyncio.run(core.generateCustomIntroduce('运维工程师', '', '负责系统运维'))

        self.assertEqual(result, '您好，我对这个岗位很感兴趣，方便进一步沟通吗？')
        self.assertEqual(mocked.await_count, 2)
        self.assertEqual(mocked.await_args_list[1].args[2], 'introduce_retry')
        self.assertEqual(mocked.await_args_list[1].args[1]['max_tokens'], 2048)

    def test_reasoning_only_stop_response_also_retries(self):
        llm_config = {
            'enabled': True, 'api_base': 'https://llm.example/v1', 'api_key': 'test-key',
            'model': 'reasoning-model', 'introduce_max_tokens': 256,
            'introduce_retry_max_tokens': 4096,
        }
        mocked = AsyncMock(side_effect=[
            {'choices': [{'finish_reason': 'stop', 'message': {'content': None, 'reasoning': 'analysis only'}}]},
            {'choices': [{'finish_reason': 'stop', 'message': {'content': '您好，我对这个岗位很感兴趣，希望有机会进一步沟通，谢谢。'}}]},
        ])
        with (
            patch.object(core.Config, 'llm', llm_config),
            patch.object(core.Config, 'resume_content', 'resume'),
            patch.object(core.LLM_GATEWAY, 'chat_completions', mocked),
        ):
            result = asyncio.run(core.generateCustomIntroduce('SRE', '', 'operations'))

        self.assertEqual(result, '您好，我对这个岗位很感兴趣，希望有机会进一步沟通，谢谢。')
        self.assertEqual(mocked.await_count, 2)
        self.assertEqual(mocked.await_args_list[1].args[1]['max_tokens'], 4096)

    def test_nonempty_truncated_planning_content_is_never_sent(self):
        llm_config = {
            'enabled': True, 'api_base': 'https://llm.example/v1', 'api_key': 'test-key',
            'model': 'reasoning-model', 'introduce_max_tokens': 256,
            'introduce_retry_max_tokens': 2048, 'reasoning_effort': 'high',
        }
        planning = '- 开头：说明自己是求职者\n- 介绍相关技能：Linux运维\n草拟内容：您好，我是求职者'
        final_greeting = '您好，我具备Linux系统运维和自动化脚本实践经验，对贵公司的运维工程师岗位很感兴趣，希望能进一步了解岗位安排，感谢您的考虑。'
        mocked = AsyncMock(side_effect=[
            {'choices': [{'finish_reason': 'length', 'message': {'content': planning, 'reasoning': '分析中'}}]},
            {'choices': [{'finish_reason': 'stop', 'message': {'content': final_greeting}}]},
        ])
        with (
            patch.object(core.Config, 'llm', llm_config),
            patch.object(core.Config, 'resume_content', 'Linux运维和自动化脚本实践经验'),
            patch.object(core.LLM_GATEWAY, 'chat_completions', mocked),
        ):
            result = asyncio.run(core.generateCustomIntroduce('运维工程师', '', '负责Linux系统运维'))

        self.assertEqual(result, final_greeting)
        self.assertEqual(mocked.await_count, 2)
        self.assertNotIn('reasoning_effort', mocked.await_args_list[0].args[1])

    def test_planning_content_with_stop_is_rejected(self):
        llm_config = {
            'enabled': True, 'api_base': 'https://llm.example/v1', 'api_key': 'test-key',
            'model': 'reasoning-model', 'introduce_max_tokens': 256,
        }
        final_greeting = '您好，我有服务器维护、故障排查及Shell脚本实践经验，对运维岗位很感兴趣，希望有机会进一步沟通，谢谢。'
        mocked = AsyncMock(side_effect=[
            {'choices': [{'finish_reason': 'stop', 'message': {'content': '分析如下：\n1. 开头：礼貌问候\n2. 收尾：询问时间'}}]},
            {'choices': [{'finish_reason': 'stop', 'message': {'content': final_greeting}}]},
        ])
        with (
            patch.object(core.Config, 'llm', llm_config),
            patch.object(core.Config, 'resume_content', '服务器维护、故障排查及Shell脚本实践经验'),
            patch.object(core.LLM_GATEWAY, 'chat_completions', mocked),
        ):
            result = asyncio.run(core.generateCustomIntroduce('运维工程师', '', '负责服务器维护'))

        self.assertEqual(result, final_greeting)
        self.assertEqual(mocked.await_count, 2)

    def test_reasoning_final_answer_is_not_promoted_to_sendable_content(self):
        llm_config = {
            'enabled': True, 'api_base': 'https://llm.example/v1', 'api_key': 'test-key',
            'model': 'reasoning-model', 'introduce_max_tokens': 256,
        }
        final_greeting = '您好，我具备网络维护和故障排查实践经验，对技术支持岗位很感兴趣，希望有机会进一步沟通，谢谢。'
        mocked = AsyncMock(side_effect=[
            {
                'choices': [{
                    'finish_reason': 'stop',
                    'message': {'content': None, 'reasoning': f'分析过程……最终答案：{final_greeting}'},
                }],
            },
            {'choices': [{'finish_reason': 'stop', 'message': {'content': final_greeting}}]},
        ])
        with (
            patch.object(core.Config, 'llm', llm_config),
            patch.object(core.Config, 'resume_content', '网络维护和故障排查实践经验'),
            patch.object(core.LLM_GATEWAY, 'chat_completions', mocked),
        ):
            result = asyncio.run(core.generateCustomIntroduce('技术支持', '', '负责网络维护'))

        self.assertEqual(result, final_greeting)
        self.assertEqual(mocked.await_count, 2)

    def test_two_invalid_responses_fall_back_to_fixed_greeting(self):
        llm_config = {
            'enabled': True, 'api_base': 'https://llm.example/v1', 'api_key': 'test-key',
            'model': 'reasoning-model', 'introduce_max_tokens': 256,
        }
        mocked = AsyncMock(side_effect=[
            {'choices': [{'finish_reason': 'stop', 'message': {'content': '开头：先问好\n收尾：再询问'}}]},
            {'choices': [{'finish_reason': 'length', 'message': {'content': '您好，我具备Linux运维经验'}}]},
        ])
        with (
            patch.object(core.Config, 'llm', llm_config),
            patch.object(core.Config, 'introduce', '固定安全招呼语'),
            patch.object(core.Config, 'resume_content', 'Linux运维经验'),
            patch.object(core.LLM_GATEWAY, 'chat_completions', mocked),
        ):
            result = asyncio.run(core.generateCustomIntroduce('运维工程师', '', '负责Linux系统运维'))

        self.assertEqual(result, '固定安全招呼语')
        self.assertEqual(mocked.await_count, 2)

    def test_single_sentence_planning_text_is_rejected(self):
        llm_config = {
            'enabled': True, 'api_base': 'https://llm.example/v1', 'api_key': 'test-key',
            'model': 'reasoning-model', 'introduce_max_tokens': 256,
        }
        final_greeting = '您好，我具备Linux系统维护和故障排查经验，对贵公司的运维岗位很感兴趣，希望有机会进一步沟通，谢谢。'
        mocked = AsyncMock(side_effect=[
            {
                'choices': [{
                    'finish_reason': 'stop',
                    'message': {'content': '首先需要从简历提取相关技能，然后组织措辞，最后生成一段礼貌回复'},
                }],
            },
            {'choices': [{'finish_reason': 'stop', 'message': {'content': final_greeting}}]},
        ])
        with (
            patch.object(core.Config, 'llm', llm_config),
            patch.object(core.Config, 'resume_content', 'Linux系统维护和故障排查经验'),
            patch.object(core.LLM_GATEWAY, 'chat_completions', mocked),
        ):
            result = asyncio.run(core.generateCustomIntroduce('运维工程师', '', '负责系统维护'))

        self.assertEqual(result, final_greeting)
        self.assertEqual(mocked.await_count, 2)

    def test_return_meta_reports_fallback_without_comparing_text(self):
        llm_config = {
            'enabled': True, 'api_base': 'https://llm.example/v1', 'api_key': 'test-key',
            'model': 'reasoning-model', 'introduce_max_tokens': 256,
        }
        mocked = AsyncMock(side_effect=RuntimeError('provider unavailable'))
        with (
            patch.object(core.Config, 'llm', llm_config),
            patch.object(core.Config, 'introduce', '固定安全招呼语'),
            patch.object(core.Config, 'resume_content', 'Linux系统维护经验'),
            patch.object(core.LLM_GATEWAY, 'chat_completions', mocked),
        ):
            result = asyncio.run(core.generateCustomIntroduce('运维工程师', '', '负责系统维护', return_meta=True))

        self.assertEqual(result['introduce'], '固定安全招呼语')
        self.assertFalse(result['generated'])
        self.assertTrue(result['fallbackReason'])

    def test_upstream_500_uses_fixed_introduce_without_traceback(self):
        output = StringIO()
        llm_config = {
            'enabled': True,
            'api_base': 'https://llm.example/v1',
            'api_key': 'test-key',
            'model': 'test-model',
            'introduce_max_tokens': 128,
            'verbose_errors': False,
        }
        error = LLMGatewayError('HTTPStatusError: upstream returned 500', status_code=500)

        with (
            patch.object(core.Config, 'llm', llm_config),
            patch.object(core.Config, 'introduce', '固定招呼语'),
            patch.object(core.Config, 'resume_content', '测试简历'),
            patch.object(core.LLM_GATEWAY, 'chat_completions', AsyncMock(side_effect=error)),
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            result = asyncio.run(core.generateCustomIntroduce('测试岗位', '', '测试职位描述'))

        self.assertEqual(result, '固定招呼语')
        self.assertIn('使用固定招呼语', output.getvalue())
        self.assertNotIn('Traceback', output.getvalue())


if __name__ == '__main__':
    unittest.main()
