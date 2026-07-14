import unittest
from unittest.mock import patch

import core


class ScoringDeductionTest(unittest.TestCase):
    def test_returns_location_keyword_and_deducted_stars(self):
        job = '# 职位名称\n销售运维工程师\n\n# 职位描述\n需要轮值夜班'
        with (
            patch.object(core.Config, 'title_deduction_keywords', {'销售': 5}),
            patch.object(core.Config, 'detail_deduction_keywords', {'夜班': 3}),
        ):
            result = core.evaluateJobMatch(job)

        self.assertEqual(result['deductedStars'], 8)
        self.assertTrue(result['discarded'])
        self.assertEqual(result['deductions'], [
            {'field': 'title', 'fieldLabel': '职位名称', 'keyword': '销售', 'deductStars': 5},
            {'field': 'detail', 'fieldLabel': '职位描述', 'keyword': '夜班', 'deductStars': 3},
        ])


if __name__ == '__main__':
    unittest.main()
