from __future__ import annotations

import unittest
from unittest.mock import patch

from app.config import Config
from app.scoring import evaluate_job_match


JOB_TEXT = """# 职位名称
Java 后端工程师

# 薪资范围
20-30K

# 职位描述
负责 Spring Boot 服务开发
"""


class ScoringSwitchTests(unittest.TestCase):
    def test_disabled_scoring_keeps_full_score(self) -> None:
        with patch.object(Config, 'scoring_enabled', False):
            result = evaluate_job_match(JOB_TEXT)

        self.assertEqual(result['score'], 100)
        self.assertEqual(result['stars'], 5)
        self.assertEqual(result['deductedStars'], 0)
        self.assertFalse(result['discarded'])
        self.assertEqual(result['deductions'], [])
        self.assertFalse(result['scoringEnabled'])
        self.assertIn('扣分规则已关闭', result['reason'])

    def test_enabled_scoring_applies_keyword_deductions(self) -> None:
        with (
            patch.object(Config, 'scoring_enabled', True),
            patch.object(Config, 'title_deduction_keywords', {'java': 2}),
            patch.object(Config, 'detail_deduction_keywords', {'spring boot': 1}),
        ):
            result = evaluate_job_match(JOB_TEXT)

        self.assertEqual(result['stars'], 2)
        self.assertEqual(result['score'], 40)
        self.assertEqual(result['deductedStars'], 3)
        self.assertTrue(result['scoringEnabled'])
        self.assertEqual(
            {(item['field'], item['keyword'], item['deductStars']) for item in result['deductions']},
            {('title', 'java', 2), ('detail', 'spring boot', 1)},
        )

if __name__ == '__main__':
    unittest.main()
