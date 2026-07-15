"""岗位评分与 LLM 任务模块的无网络单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.config import Config
from app.scoring import evaluate_job_match, parse_job_fields
from app.llm import tasks as llm_tasks
from app.routes import delivery as delivery_routes


JOB_TEXT = """# 职位名称
系统运维工程师

# 薪资范围
20-30K·14薪

# 职位描述
负责平台稳定性，不涉及销售。

需要编写自动化工具。
"""


class JobScoringTests(unittest.TestCase):
    def test_parse_job_fields_returns_title_salary_and_full_detail(self):
        title, salary, detail = parse_job_fields(JOB_TEXT)

        self.assertEqual(title, '系统运维工程师')
        self.assertEqual(salary, '20-30K·14薪')
        self.assertEqual(detail, '负责平台稳定性，不涉及销售。\n\n需要编写自动化工具。')

    def test_evaluate_job_match_prefers_long_keyword_and_returns_salary(self):
        with patch.object(
            Config,
            'title_deduction_keywords',
            {'运维': 1, '系统运维工程师': 2},
        ), patch.object(Config, 'detail_deduction_keywords', {'销售': 2}):
            result = evaluate_job_match(JOB_TEXT)

        self.assertEqual(result['salary'], '20-30K·14薪')
        self.assertEqual(result['deductedStars'], 4)
        self.assertEqual(result['stars'], 1)
        self.assertEqual(result['score'], 20)
        self.assertFalse(result['discarded'])
        self.assertEqual(
            [item['keyword'] for item in result['deductions']],
            ['系统运维工程师', '销售'],
        )

    def test_score_below_zero_is_clamped_and_discarded(self):
        with patch.object(Config, 'title_deduction_keywords', {'系统运维工程师': 5}), patch.object(
            Config,
            'detail_deduction_keywords',
            {'销售': 2},
        ):
            result = evaluate_job_match(JOB_TEXT)

        self.assertEqual(result['rawStars'], -2)
        self.assertEqual(result['stars'], 0)
        self.assertEqual(result['score'], 0)
        self.assertTrue(result['discarded'])

class LLMTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_llm_greeting_uses_fixed_text_without_calling_model(self):
        payload = {'company': '示例公司', 'title': '平台工程师', 'salary': '20-30K', 'detail': '岗位描述'}
        generator = AsyncMock()
        with patch.object(Config, 'llm_greeting_enabled', False), patch.object(
            Config, 'introduce', '固定招呼语'
        ), patch.object(delivery_routes, 'generate_custom_introduce', generator):
            result = await delivery_routes._generate_introduce_result(payload)

        generator.assert_not_awaited()
        self.assertEqual(result, {
            'introduce': '固定招呼语',
            'generated': False,
            'fallbackReason': 'LLM 打招呼已关闭',
        })

    async def test_enabled_llm_greeting_keeps_existing_generation_flow(self):
        payload = {'company': '示例公司', 'title': '平台工程师', 'salary': '20-30K', 'detail': '岗位描述'}
        expected = {'introduce': '动态招呼语', 'generated': True, 'fallbackReason': None}
        generator = AsyncMock(return_value=expected)
        with patch.object(Config, 'llm_greeting_enabled', True), patch.object(
            delivery_routes, 'generate_custom_introduce', generator
        ):
            result = await delivery_routes._generate_introduce_result(payload)

        generator.assert_awaited_once_with('平台工程师', '20-30K', '岗位描述', return_meta=True)
        self.assertEqual(result, expected)

    async def test_salary_is_in_job_filter_prompt_without_network(self):
        manager = SimpleNamespace(
            job_filter_enabled=True,
            available=lambda: True,
            chat_completions=AsyncMock(
                return_value={
                    'choices': [
                        {'message': {'content': 'true\n岗位与简历匹配'}, 'finish_reason': 'stop'}
                    ]
                }
            ),
        )
        with patch.object(llm_tasks, 'LLM_MANAGER', manager), patch.object(
            llm_tasks,
            'load_resume',
            return_value='熟悉 Python 自动化与平台工程。',
        ):
            passed, reason = await llm_tasks.llm_job_filter(
                '平台工程师',
                '20-30K·14薪',
                '负责自动化平台建设',
            )

        payload, purpose = manager.chat_completions.await_args.args
        self.assertTrue(passed)
        self.assertEqual(reason, '岗位与简历匹配')
        self.assertEqual(purpose, 'job_filter')
        self.assertIn('薪资范围：20-30K·14薪', payload['messages'][1]['content'])

    async def test_salary_is_in_introduce_prompt_without_network(self):
        greeting = '您好，我有Python自动化和平台工程项目经验，与岗位方向较匹配，希望进一步了解团队和具体工作内容。'
        manager = SimpleNamespace(
            available=lambda: True,
            chat_completions=AsyncMock(
                return_value={
                    'choices': [
                        {'message': {'content': greeting}, 'finish_reason': 'stop'}
                    ]
                }
            ),
        )
        with patch.object(llm_tasks, 'LLM_MANAGER', manager), patch.object(
            llm_tasks,
            'load_resume',
            return_value='熟悉 Python 自动化与平台工程。',
        ):
            result = await llm_tasks.generate_custom_introduce(
                '平台工程师',
                '20-30K·14薪',
                '负责自动化平台建设',
                return_meta=True,
            )

        payload, purpose = manager.chat_completions.await_args.args
        self.assertEqual(result['introduce'], greeting)
        self.assertTrue(result['generated'])
        self.assertEqual(purpose, 'introduce')
        self.assertIn('薪资范围：20-30K·14薪', payload['messages'][1]['content'])


if __name__ == '__main__':
    unittest.main()
