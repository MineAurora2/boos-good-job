from __future__ import annotations

import unittest
from unittest.mock import patch

from app.config import Config, DEFAULT_USER_CONFIG
from app.scoring import evaluate_job_match


class DeliveryV2ProtocolTests(unittest.TestCase):
    def test_protocol_versions_and_client_contract_are_v2(self) -> None:
        from app.protocol import CONTROL_PROTOCOL_VERSION, SCRIPT_API_VERSION

        self.assertEqual(SCRIPT_API_VERSION, 2)
        self.assertEqual(CONTROL_PROTOCOL_VERSION, 2)
        client = Config.get_client_config()
        self.assertEqual(client['scriptApiVersion'], 2)
        self.assertTrue(client['qualificationRequired'])
        self.assertIs(client['scoringEnabled'], Config.scoring_enabled)
        self.assertIs(client['llmGreetingEnabled'], Config.llm_greeting_enabled)

    def test_removed_random_empty_greeting_setting_is_not_exposed(self) -> None:
        self.assertNotIn('randomNoIntroduceRatio', DEFAULT_USER_CONFIG['frontend'])
        self.assertNotIn('randomNoIntroduceRatio', Config.get_client_config()['frontend'])


class QualificationTokenTests(unittest.TestCase):
    def test_qualification_fingerprint_is_stable_and_mode_bound(self) -> None:
        from app.qualification import build_qualification_context

        common = dict(
            company='Acme', title='SRE', salary='20K', detail='Linux', job_url='/job/1',
            scoring_config={'enabled': True}, resume='resume', filter_prompt='prompt',
            llm_config={'strategy': 'failover'},
        )
        delivery = build_qualification_context(**common, mode='delivery')
        repeated = build_qualification_context(**common, mode='delivery')
        scan = build_qualification_context(**common, mode='scan')
        self.assertEqual(
            delivery['qualificationFingerprint'], repeated['qualificationFingerprint'],
        )
        self.assertNotEqual(
            delivery['qualificationFingerprint'], scan['qualificationFingerprint'],
        )

    def test_token_is_bound_to_every_gate_identity_field(self) -> None:
        from app.qualification import QualificationTokenManager

        manager = QualificationTokenManager(b'test-secret', ttl_seconds=300)
        claims = {
            'company': 'Example Co',
            'title': 'SRE',
            'accountId': 'account-1',
            'workerId': 'worker-1',
            'jobFingerprint': 'job-fp',
            'configFingerprint': 'config-fp',
            'mode': 'delivery',
        }
        token, expires_at = manager.issue(claims, now=1_000)

        self.assertEqual(expires_at, 1_300)
        verified = manager.verify(token, expected=claims, now=1_299)
        self.assertEqual(verified['company'], 'Example Co')
        for field in claims:
            changed = dict(claims)
            changed[field] = f'{claims[field]}-changed'
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'qualification_mismatch'):
                manager.verify(token, expected=changed, now=1_299)

    def test_expired_or_tampered_token_is_rejected(self) -> None:
        from app.qualification import QualificationTokenManager

        manager = QualificationTokenManager(b'test-secret', ttl_seconds=300)
        token, _ = manager.issue({
            'company': 'A', 'title': 'B', 'accountId': 'C', 'workerId': 'D',
            'jobFingerprint': 'E', 'configFingerprint': 'F', 'mode': 'scan',
        }, now=1_000)
        with self.assertRaisesRegex(ValueError, 'qualification_expired'):
            manager.verify(token, now=1_300)
        with self.assertRaisesRegex(ValueError, 'invalid_qualification_token'):
            manager.verify(token[:-1] + ('A' if token[-1] != 'A' else 'B'), now=1_100)

    def test_token_verification_supports_chinese_bound_fields(self) -> None:
        from app.qualification import QualificationTokenManager

        manager = QualificationTokenManager(b'test-secret', ttl_seconds=300)
        claims = {
            'company': '示例公司',
            'title': '运维工程师',
            'accountId': '账号一',
            'workerId': '浏览器一',
            'jobFingerprint': '岗位指纹',
            'configFingerprint': '配置指纹',
            'mode': 'delivery',
        }
        token, _ = manager.issue(claims, now=1_000)

        verified = manager.verify(token, expected=claims, now=1_100)

        self.assertEqual(verified['company'], claims['company'])
        self.assertEqual(verified['title'], claims['title'])

    def test_fingerprints_change_only_with_their_owned_inputs(self) -> None:
        from app.qualification import build_qualification_context

        base = build_qualification_context(
            company=' Acme ', title='SRE', salary='20K', detail='Kubernetes', job_url='/job/1',
            scoring_config={'enabled': True, 'threshold': 3},
            resume='resume-v1', filter_prompt='prompt-v1',
            llm_config={'strategy': 'failover', 'providers': [{'model': 'm1'}]},
            filter_version='2',
        )
        same = build_qualification_context(
            company='Acme', title='SRE', salary='20K', detail='Kubernetes', job_url='/job/1',
            scoring_config={'threshold': 3, 'enabled': True},
            resume='resume-v1', filter_prompt='prompt-v1',
            llm_config={'providers': [{'model': 'm1'}], 'strategy': 'failover'},
            filter_version='2',
        )
        self.assertEqual(base, same)

        changed_job = build_qualification_context(
            company='Acme', title='SRE', salary='20K', detail='Linux', job_url='/job/1',
            scoring_config={'enabled': True, 'threshold': 3},
            resume='resume-v1', filter_prompt='prompt-v1',
            llm_config={'strategy': 'failover', 'providers': [{'model': 'm1'}]},
            filter_version='2',
        )
        self.assertNotEqual(base['jobFingerprint'], changed_job['jobFingerprint'])
        self.assertNotEqual(base['aiFingerprint'], changed_job['aiFingerprint'])
        self.assertEqual(base['configFingerprint'], changed_job['configFingerprint'])

        changed_resume = build_qualification_context(
            company='Acme', title='SRE', salary='20K', detail='Kubernetes', job_url='/job/1',
            scoring_config={'enabled': True, 'threshold': 3},
            resume='resume-v2', filter_prompt='prompt-v1',
            llm_config={'strategy': 'failover', 'providers': [{'model': 'm1'}]},
            filter_version='2',
        )
        self.assertEqual(base['jobFingerprint'], changed_resume['jobFingerprint'])
        self.assertEqual(base['configFingerprint'], changed_resume['configFingerprint'])
        self.assertNotEqual(base['aiFingerprint'], changed_resume['aiFingerprint'])

        different_url = build_qualification_context(
            company='Acme', title='SRE', salary='20K', detail='Kubernetes',
            job_url='/web/chat/index?from=search',
            scoring_config={'enabled': True, 'threshold': 3},
            resume='resume-v1', filter_prompt='prompt-v1',
            llm_config={'strategy': 'failover', 'providers': [{'model': 'm1'}]},
            filter_version='2',
        )
        self.assertNotEqual(base['jobFingerprint'], different_url['jobFingerprint'])
        self.assertNotEqual(
            base['qualificationFingerprint'], different_url['qualificationFingerprint'],
        )
        self.assertEqual(base['aiFingerprint'], different_url['aiFingerprint'])


class NormalizedScoringTests(unittest.TestCase):
    @staticmethod
    def job(title: str, detail: str) -> str:
        return f'# 职位名称\n{title}\n# 薪资范围\n20K\n# 职位描述\n{detail}'

    def test_nfkc_casefold_and_whitespace_are_normalized(self) -> None:
        with (
            patch.object(Config, 'scoring_enabled', True),
            patch.object(Config, 'title_deduction_keywords', {'ＡＩ  ENGINEER': 2}),
            patch.object(Config, 'detail_deduction_keywords', {}),
        ):
            result = evaluate_job_match(self.job('ai\t engineer', ''))
        self.assertEqual(result['deductedStars'], 2)

    def test_english_keywords_use_word_boundaries_but_chinese_uses_substrings(self) -> None:
        with (
            patch.object(Config, 'scoring_enabled', True),
            patch.object(Config, 'title_deduction_keywords', {'java': 2, '销售': 3}),
            patch.object(Config, 'detail_deduction_keywords', {}),
        ):
            result = evaluate_job_match(self.job('JavaScript 销售顾问', ''))
        self.assertEqual(result['deductedStars'], 3)
        self.assertEqual([item['keyword'] for item in result['deductions']], ['销售'])

    def test_same_keyword_deducts_at_most_once_per_field(self) -> None:
        with (
            patch.object(Config, 'scoring_enabled', True),
            patch.object(Config, 'title_deduction_keywords', {'java': 2}),
            patch.object(Config, 'detail_deduction_keywords', {'java': 1}),
        ):
            result = evaluate_job_match(self.job('Java java JAVA', 'java and java'))
        self.assertEqual(result['deductedStars'], 3)
        self.assertEqual(len(result['deductions']), 2)

    def test_distinct_overlapping_keywords_each_deduct_once(self) -> None:
        with (
            patch.object(Config, 'scoring_enabled', True),
            patch.object(Config, 'title_deduction_keywords', {
                '算法': 2,
                '算法工程师': 3,
            }),
            patch.object(Config, 'detail_deduction_keywords', {}),
        ):
            result = evaluate_job_match(self.job('算法工程师', ''))

        self.assertEqual(result['deductedStars'], 5)
        self.assertCountEqual(
            [item['keyword'] for item in result['deductions']],
            ['算法', '算法工程师'],
        )

    def test_ascii_keywords_with_symbols_still_use_word_boundaries(self) -> None:
        with (
            patch.object(Config, 'scoring_enabled', True),
            patch.object(Config, 'title_deduction_keywords', {'c++': 2}),
            patch.object(Config, 'detail_deduction_keywords', {}),
        ):
            embedded = evaluate_job_match(self.job('XC++ and C++Builder', ''))
            standalone = evaluate_job_match(self.job('C++ engineer', ''))
        self.assertEqual(embedded['deductedStars'], 0)
        self.assertEqual(standalone['deductedStars'], 2)


if __name__ == '__main__':
    unittest.main()
