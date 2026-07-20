from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from app.config import Config
from app.llm.manager import LLM_MANAGER
from app.routes import delivery as delivery_routes
from app.runtime import RUNTIME_MONITOR
from app.state import STATE
from app.storage.delivery_store import DeliveryStore


class DeliveryV2RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(__file__).parent / f'.test_delivery_routes_{uuid.uuid4().hex}.db'
        self.store = DeliveryStore(self.db_path, daily_limit=20)
        self.original_store = STATE._delivery_store
        STATE._delivery_store = self.store
        self.policy = {
            'safety': {
                'globalPaused': False,
                'scanOnly': False,
                'scanAiEnabled': False,
                'sendingDisabled': False,
                'openingDisabled': False,
                'resumeSendingDisabled': False,
                'stopOnDailyLimit': True,
                'stopOnServiceError': True,
            },
            'plan': {
                'dailyTarget': 0,
                'hourlyLimit': 0,
                'activeStart': '',
                'activeEnd': '',
                'breakStart': '',
                'breakEnd': '',
                'stopAtTarget': True,
                'maxConsecutiveFailures': 3,
                'minDelayMs': 0,
                'maxDelayMs': 0,
            },
            'account': {'accountId': 'account-1', 'dailyLimit': 20},
            'policy': {},
            'shouldPause': False,
            'workerId': 'worker-1',
        }
        self.effective = patch.object(
            RUNTIME_MONITOR,
            'effective_control',
            side_effect=lambda _worker, _account: deepcopy(self.policy),
        )
        self.audit = patch.object(RUNTIME_MONITOR, 'audit')
        self.record_decision = patch.object(STATE, 'record_job_decision')
        self.record_filter = patch.object(STATE, 'record_ai_filter')
        self.effective.start()
        self.audit_mock = self.audit.start()
        self.record_decision.start()
        self.record_filter.start()

    def tearDown(self) -> None:
        patch.stopall()
        STATE._delivery_store = self.original_store
        for suffix in ('', '-wal', '-shm'):
            Path(f'{self.db_path}{suffix}').unlink(missing_ok=True)

    @staticmethod
    def job(**overrides) -> dict:
        payload = {
            'scriptApiVersion': 2,
            'mode': 'delivery',
            'company': 'Acme',
            'title': 'SRE',
            'salary': '20-30K',
            'detail': 'Maintain Linux and Kubernetes platforms',
            'jobUrl': 'https://example.test/job/1',
            'accountId': 'account-1',
            'workerId': 'worker-1',
            'hrActiveLevel': 'today',
        }
        payload.update(overrides)
        return payload

    def qualify(self, **overrides) -> dict:
        return delivery_routes.qualify_delivery(self.job(**overrides))

    def claim(self, qualification: dict, **overrides) -> dict:
        payload = self.job(**overrides)
        return delivery_routes.claim_delivery({
            'scriptApiVersion': 2,
            'qualificationToken': qualification['qualificationToken'],
            'company': payload['company'],
            'title': payload['title'],
            'accountId': payload['accountId'],
            'workerId': payload['workerId'],
            'jobUrl': payload['jobUrl'],
        })

    def test_connection_metadata_reports_both_v2_versions(self) -> None:
        route = next(route for route in main.create_app().routes if route.path == '/api/connection')
        payload = asyncio.run(route.endpoint())
        self.assertEqual(payload['scriptApiVersion'], 2)
        self.assertEqual(payload['protocolVersion'], 2)

    def test_get_job_score_is_pure_rules_and_never_calls_llm(self) -> None:
        job = '# 职位名称\nSRE\n# 薪资范围\n20K\n# 职位描述\nLinux'
        with patch.object(
            delivery_routes,
            'strict_llm_job_filter',
            new=AsyncMock(side_effect=AssertionError('LLM must not run')),
        ) as llm:
            result = asyncio.run(delivery_routes.get_job_score({
                'scriptApiVersion': 2,
                'job': job,
            }))
        llm.assert_not_awaited()
        self.assertNotIn('aiPassed', result)
        self.assertFalse(result['aiFilterEnabled'])

    def test_get_job_score_rejects_legacy_script_payloads(self) -> None:
        job = '# 职位名称\nSRE\n# 薪资范围\n20K\n# 职位描述\nLinux'
        legacy_payloads = (
            job,
            {'scriptApiVersion': 1, 'job': job},
        )

        for payload in legacy_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(delivery_routes.get_job_score(payload))
                self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(error.exception.detail, 'script_api_version_mismatch')

    def test_get_job_score_http_contract_rejects_legacy_and_keeps_v2_pure(self) -> None:
        job = '# 职位名称\nSRE\n# 薪资范围\n20K\n# 职位描述\nLinux'
        client = TestClient(main.create_app(), client=('127.0.0.1', 50000))
        try:
            legacy_string = client.post('/get-job-score', json=job)
            legacy_v1 = client.post(
                '/get-job-score',
                json={'scriptApiVersion': 1, 'job': job},
            )
            with patch.object(
                delivery_routes,
                'strict_llm_job_filter',
                new=AsyncMock(side_effect=AssertionError('LLM must not run')),
            ) as llm:
                v2 = client.post(
                    '/get-job-score',
                    json={'scriptApiVersion': 2, 'job': job},
                )
        finally:
            client.close()

        self.assertEqual(legacy_string.status_code, 409)
        self.assertEqual(legacy_string.json(), {'detail': 'script_api_version_mismatch'})
        self.assertEqual(legacy_v1.status_code, 409)
        self.assertEqual(legacy_v1.json(), {'detail': 'script_api_version_mismatch'})
        self.assertEqual(v2.status_code, 200)
        self.assertEqual(v2.json()['title'], 'SRE')
        self.assertFalse(v2.json()['aiFilterEnabled'])
        self.assertNotIn('aiPassed', v2.json())
        llm.assert_not_awaited()

    def test_get_job_score_http_rejects_missing_or_invalid_job_text(self) -> None:
        invalid_payloads = {
            'missing': {'scriptApiVersion': 2},
            'empty': {'scriptApiVersion': 2, 'job': ''},
            'whitespace': {'scriptApiVersion': 2, 'job': '  \r\n\t'},
            'array': {'scriptApiVersion': 2, 'job': ['SRE']},
            'object': {'scriptApiVersion': 2, 'job': {'title': 'SRE'}},
        }
        client = TestClient(main.create_app(), client=('127.0.0.1', 50000))
        try:
            for name, payload in invalid_payloads.items():
                with self.subTest(name=name):
                    response = client.post('/get-job-score', json=payload)
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.json(), {'detail': 'invalid_job'})
        finally:
            client.close()

    def test_old_or_missing_script_version_is_fail_closed(self) -> None:
        with self.assertRaises(HTTPException) as qualify_error:
            delivery_routes.qualify_delivery(self.job(scriptApiVersion=1))
        self.assertEqual(qualify_error.exception.status_code, 409)

        with self.assertRaises(HTTPException) as claim_error:
            delivery_routes.claim_delivery({'scriptApiVersion': 1})
        self.assertEqual(claim_error.exception.status_code, 409)

        with self.assertRaises(HTTPException) as filter_error:
            asyncio.run(delivery_routes.start_job_filter({'scriptApiVersion': 1}))
        self.assertEqual(filter_error.exception.status_code, 409)

    def test_qualify_validates_fields_and_duplicate_before_scoring_or_ai(self) -> None:
        missing = self.qualify(jobUrl='')
        self.assertFalse(missing['allowed'])
        self.assertEqual(missing['reason'], 'missing_job_url')

        self.store.record_legacy_sent('Acme', 'SRE')
        with (
            patch.object(delivery_routes, 'evaluate_job_match') as scoring,
            patch.object(delivery_routes, 'strict_llm_job_filter', new=AsyncMock(), create=True) as llm,
        ):
            duplicate = self.qualify()
        self.assertFalse(duplicate['allowed'])
        self.assertEqual(duplicate['reason'], 'duplicate_job')
        scoring.assert_not_called()
        llm.assert_not_awaited()

    def test_qualify_issues_bound_token_then_claim_rechecks_policy_atomically(self) -> None:
        with patch.object(delivery_routes, 'strict_llm_job_filter', new=AsyncMock(), create=True) as llm:
            qualification = self.qualify()
        self.assertTrue(qualification['success'])
        self.assertTrue(qualification['allowed'])
        self.assertEqual(qualification['reason'], 'qualified')
        self.assertTrue(qualification['qualificationToken'])
        self.assertTrue(qualification['qualificationFingerprint'])
        llm.assert_not_awaited()

        claimed = self.claim(qualification)
        self.assertTrue(claimed['accepted'])
        self.assertEqual(claimed['qualificationFingerprint'], qualification['qualificationFingerprint'])
        self.assertGreater(claimed['leaseExpiresAt'], 0)

        invalid_binding = delivery_routes.claim_delivery({
            'scriptApiVersion': 2,
            'qualificationToken': qualification['qualificationToken'],
            'company': 'Different Company',
            'title': 'SRE',
            'accountId': 'account-1',
            'workerId': 'worker-1',
            'jobUrl': 'https://example.test/job/1',
        })
        self.assertFalse(invalid_binding['allowed'])
        self.assertEqual(invalid_binding['reason'], 'qualification_mismatch')

    def test_claim_retry_returns_same_reserved_token_without_double_counting(self) -> None:
        qualification = self.qualify()

        first = self.claim(qualification)
        retried = self.claim(qualification)

        self.assertTrue(first['accepted'])
        self.assertTrue(retried['accepted'])
        self.assertTrue(retried['idempotent'])
        self.assertEqual(retried['claimToken'], first['claimToken'])
        self.assertEqual(self.store.quota_status('account-1')['count'], 1)

    def test_chinese_company_and_title_can_qualify_and_claim(self) -> None:
        with patch.object(LLM_MANAGER, '_job_filter', False):
            qualification = self.qualify(company='示例公司', title='运维工程师')
            claimed = self.claim(
                qualification,
                company='示例公司',
                title='运维工程师',
            )

        self.assertTrue(qualification['allowed'])
        self.assertTrue(claimed['accepted'])

    def test_rules_hr_and_server_policy_all_short_circuit_before_token(self) -> None:
        with (
            patch.object(Config, 'scoring_enabled', True),
            patch.object(Config, 'title_deduction_keywords', {'java': 3}),
            patch.object(Config, 'detail_deduction_keywords', {}),
            patch.dict(Config.frontend, {'thread': 80}),
        ):
            rules = self.qualify(title='Java engineer')
        self.assertFalse(rules['allowed'])
        self.assertEqual(rules['reason'], 'score_below_threshold')
        self.assertNotIn('qualificationToken', rules)

        with patch.dict(Config.frontend, {
            'hrActiveFilterEnabled': True,
            'hrActiveLevels': ['today'],
        }):
            hr = self.qualify(hrActiveLevel='this_week')
        self.assertFalse(hr['allowed'])
        self.assertEqual(hr['reason'], 'hr_inactive')

        self.policy['safety']['sendingDisabled'] = True
        policy = self.qualify()
        self.assertFalse(policy['allowed'])
        self.assertEqual(policy['reason'], 'sending_disabled')

        self.policy['safety']['sendingDisabled'] = False
        self.policy['consecutiveFailures'] = 3
        failures = self.qualify()
        self.assertFalse(failures['allowed'])
        self.assertEqual(failures['reason'], 'consecutive_failures')

    def test_claim_rechecks_consecutive_failures_after_qualification(self) -> None:
        qualification = self.qualify()
        self.policy['consecutiveFailures'] = 3
        self.policy['policy']['consecutiveFailures'] = 3
        claimed = self.claim(qualification)
        self.assertFalse(claimed['accepted'])
        self.assertEqual(claimed['reason'], 'consecutive_failures')

    def test_claim_refreshes_policy_after_acquiring_the_sqlite_write_lock(self) -> None:
        qualification = self.qualify()
        allowed = deepcopy(self.policy)
        blocked = deepcopy(self.policy)
        blocked['safety']['sendingDisabled'] = True

        with patch.object(
            RUNTIME_MONITOR,
            'effective_control',
            side_effect=[allowed, blocked],
        ) as effective:
            claimed = self.claim(qualification)

        self.assertEqual(effective.call_count, 2)
        self.assertFalse(claimed['accepted'])
        self.assertEqual(claimed['reason'], 'sending_disabled')
        self.assertFalse(self.store.company_status('Acme', 'SRE')['exists'])

    def test_active_and_break_windows_are_enforced_by_server_time(self) -> None:
        self.policy['plan'].update({
            'activeStart': '09:00', 'activeEnd': '18:00',
            'breakStart': '12:00', 'breakEnd': '13:00',
        })
        self.assertEqual(
            delivery_routes._policy_denial(self.policy, 'delivery', now_hhmm='08:59'),
            'outside_active_hours',
        )
        self.assertEqual(
            delivery_routes._policy_denial(self.policy, 'delivery', now_hhmm='12:30'),
            'break_window',
        )
        self.assertEqual(
            delivery_routes._policy_denial(self.policy, 'delivery', now_hhmm='14:00'),
            '',
        )

    def test_startup_quota_preflight_uses_effective_runtime_policy(self) -> None:
        for index in range(2):
            claimed = self.store.claim(
                company=f'Company {index}',
                title='SRE',
                account_id='account-1',
                worker_id='worker-1',
                qualification_fingerprint=f'qualification-{index}',
            )
            self.assertTrue(claimed['accepted'])
        self.policy['account']['dailyLimit'] = 7
        self.policy['plan'].update({
            'dailyTarget': 2,
            'hourlyLimit': 3,
            'minDelayMs': 4000,
        })

        result = delivery_routes.check_daily_limit({
            'accountId': 'account-1',
            'workerId': 'worker-1',
        })

        self.assertEqual(result['count'], 2)
        self.assertEqual(result['dailyLimit'], 7)
        self.assertEqual(result['dailyTarget'], 2)
        self.assertEqual(result['hourlyLimit'], 3)
        self.assertEqual(result['minIntervalMs'], 4000)
        self.assertTrue(result['reached'])
        self.assertEqual(result['reason'], 'daily_target')

    def test_normal_ai_requires_reserved_claim_and_scan_ai_requires_all_server_gates(self) -> None:
        qualification = self.qualify()
        missing_claim = asyncio.run(delivery_routes.start_job_filter({
            **self.job(),
            'qualificationToken': qualification['qualificationToken'],
        }))
        self.assertFalse(missing_claim['allowed'])
        self.assertEqual(missing_claim['reason'], 'claim_required')

        self.policy['safety']['scanOnly'] = True
        scan_qualification = self.qualify(mode='scan')
        scan_denied = asyncio.run(delivery_routes.start_job_filter({
            **self.job(mode='scan'),
            'qualificationToken': scan_qualification['qualificationToken'],
            'scanAiEnabled': True,
        }))
        self.assertFalse(scan_denied['allowed'])
        self.assertEqual(scan_denied['reason'], 'scan_ai_disabled')

    def test_enabled_ai_is_fail_closed_when_provider_is_temporarily_unavailable(self) -> None:
        with (
            patch.object(LLM_MANAGER, '_job_filter', True),
            patch.object(LLM_MANAGER, 'available', return_value=False),
        ):
            qualification = self.qualify()
            claimed = self.claim(qualification)
            result = asyncio.run(delivery_routes.start_job_filter({
                **self.job(),
                'qualificationToken': qualification['qualificationToken'],
                'claimToken': claimed['claimToken'],
            }))

        self.assertTrue(qualification['aiFilterEnabled'])
        self.assertTrue(claimed['aiRequired'])
        self.assertFalse(result['allowed'])
        self.assertEqual(result['reason'], 'ai_unavailable')
        self.assertFalse(self.store.claim_status(claimed['claimToken'])['exists'])

    def test_reliable_ai_rejection_is_cached_and_releases_claim(self) -> None:
        qualification = self.qualify()
        claimed = self.claim(qualification)
        started = self.store.start_ai_evaluation(
            qualification['aiFingerprint'], 'worker-1', lease_seconds=900,
        )
        with patch.object(
            delivery_routes,
            'strict_llm_job_filter',
            new=AsyncMock(return_value=(False, 'experience mismatch', True)),
            create=True,
        ):
            asyncio.run(delivery_routes._run_job_filter_evaluation(
                started['evaluationId'],
                'worker-1',
                qualification['aiFingerprint'],
                self.job(),
                claimed['claimToken'],
            ))

        self.assertFalse(self.store.claim_status(claimed['claimToken'])['exists'])
        cached = self.store.get_ai_decision(qualification['aiFingerprint'])
        self.assertFalse(cached['passed'])
        self.assertEqual(cached['reason'], 'experience mismatch')
        released = [
            call for call in self.audit_mock.call_args_list
            if call.args and call.args[0] == 'delivery_claim_released'
        ]
        self.assertEqual(len(released), 1)
        self.assertEqual(released[0].args[1]['claimToken'], claimed['claimToken'])
        self.assertEqual(released[0].args[1]['reason'], 'ai_rejected')
        self.assertEqual(released[0].args[1]['company'], 'Acme')
        completed = [
            call for call in self.audit_mock.call_args_list
            if call.args and call.args[0] == 'delivery_gate_ai_completed'
        ]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].args[1]['claimToken'], claimed['claimToken'])
        self.assertEqual(completed[0].args[1]['company'], 'Acme')
        self.assertEqual(completed[0].args[1]['title'], 'SRE')
        self.assertEqual(completed[0].args[1]['workerId'], 'worker-1')

    def test_ai_required_claim_blocks_greeting_and_queued_until_reliable_pass(self) -> None:
        with (
            patch.object(LLM_MANAGER, '_job_filter', True),
            patch.object(LLM_MANAGER, 'available', return_value=True),
        ):
            qualification = self.qualify()
            claimed = self.claim(qualification)

        with self.assertRaises(HTTPException) as greeting_error:
            delivery_routes._validate_introduce_payload({
                **self.job(), 'claimToken': claimed['claimToken'],
            })
        self.assertEqual(greeting_error.exception.status_code, 409)
        queued = delivery_routes.mark_delivery({
            'claimToken': claimed['claimToken'], 'status': 'queued',
        })
        self.assertFalse(queued['success'])
        self.assertEqual(queued['reason'], 'ai_not_approved')

        self.store.save_ai_decision(
            qualification['aiFingerprint'], True, 'approved', reliable=True,
        )
        normalized = delivery_routes._validate_introduce_payload({
            **self.job(), 'claimToken': claimed['claimToken'],
        })
        self.assertEqual(normalized['claimToken'], claimed['claimToken'])
        self.assertTrue(delivery_routes.mark_delivery({
            'claimToken': claimed['claimToken'], 'status': 'queued',
        })['success'])

    def test_queued_transition_rechecks_live_server_policy(self) -> None:
        with patch.object(LLM_MANAGER, '_job_filter', False):
            qualification = self.qualify()
            claimed = self.claim(qualification)
        self.policy['safety']['sendingDisabled'] = True

        result = delivery_routes.mark_delivery({
            'claimToken': claimed['claimToken'],
            'status': 'queued',
        })

        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'sending_disabled')
        status = self.store.claim_status(claimed['claimToken'])
        self.assertEqual(status['delivery']['status'], 'reserved')

    def test_idempotent_queued_retry_still_rechecks_live_policy(self) -> None:
        with patch.object(LLM_MANAGER, '_job_filter', False):
            qualification = self.qualify()
            claimed = self.claim(qualification)
        request = {'claimToken': claimed['claimToken'], 'status': 'queued'}
        self.assertTrue(delivery_routes.mark_delivery(request)['success'])
        self.policy['safety']['sendingDisabled'] = True

        retried = delivery_routes.mark_delivery(request)

        self.assertFalse(retried['success'])
        self.assertEqual(retried['reason'], 'sending_disabled')
        self.assertEqual(retried['status'], 'queued')

    def test_disabling_ai_after_claim_clears_requirement_without_caching(self) -> None:
        with (
            patch.object(LLM_MANAGER, '_job_filter', True),
            patch.object(LLM_MANAGER, 'available', return_value=True),
        ):
            qualification = self.qualify()
            claimed = self.claim(qualification)
        self.assertTrue(claimed['aiRequired'])

        with patch.object(LLM_MANAGER, '_job_filter', False):
            skipped = asyncio.run(delivery_routes.start_job_filter({
                **self.job(),
                'qualificationToken': qualification['qualificationToken'],
                'claimToken': claimed['claimToken'],
            }))

        self.assertTrue(skipped['allowed'])
        self.assertEqual(skipped['reason'], 'ai_disabled')
        claim = self.store.claim_status(claimed['claimToken'])['delivery']
        self.assertFalse(claim['ai_required'])
        self.assertIsNone(self.store.get_ai_decision(qualification['aiFingerprint']))
        self.assertTrue(delivery_routes.mark_delivery({
            'claimToken': claimed['claimToken'], 'status': 'queued',
        })['success'])

    def test_mark_delivery_audits_state_with_claim_and_job_identity(self) -> None:
        with patch.object(LLM_MANAGER, '_job_filter', False):
            qualification = self.qualify()
            claimed = self.claim(qualification)
        self.audit_mock.reset_mock()

        result = delivery_routes.mark_delivery({
            'claimToken': claimed['claimToken'],
            'status': 'queued',
        })

        self.assertTrue(result['success'])
        self.audit_mock.assert_called_once_with(
            'delivery_state_queued',
            {
                'claimToken': claimed['claimToken'],
                'company': 'Acme',
                'title': 'SRE',
                'success': True,
                'reason': '',
                'idempotent': False,
            },
            actor='delivery',
        )

    def test_release_delivery_audits_claim_and_job_identity(self) -> None:
        with patch.object(LLM_MANAGER, '_job_filter', False):
            qualification = self.qualify()
            claimed = self.claim(qualification)
        self.audit_mock.reset_mock()

        result = delivery_routes.release_delivery({
            'claimToken': claimed['claimToken'],
            'reason': 'paused_before_side_effect',
        })

        self.assertTrue(result['success'])
        self.audit_mock.assert_called_once_with(
            'delivery_claim_released',
            {
                'claimToken': claimed['claimToken'],
                'company': 'Acme',
                'title': 'SRE',
                'success': True,
                'reason': 'paused_before_side_effect',
            },
            actor='delivery',
        )

    def test_fixed_greeting_fallback_is_audited_after_gate_validation(self) -> None:
        with patch.object(LLM_MANAGER, '_job_filter', False):
            qualification = self.qualify()
            claimed = self.claim(qualification)
        payload = self.job(claimToken=claimed['claimToken'])
        self.audit_mock.reset_mock()

        with patch.object(Config, 'llm_greeting_enabled', False):
            result = asyncio.run(delivery_routes.generate_introduce(payload))

        self.assertFalse(result['generated'])
        self.assertTrue(result['introduce'])
        calls = self.audit_mock.call_args_list
        self.assertEqual(
            [call.args[0] for call in calls],
            [
                'delivery_greeting_started',
                'delivery_greeting_fixed_fallback',
                'delivery_greeting_completed',
            ],
        )
        for call in calls:
            self.assertEqual(call.args[1]['claimToken'], claimed['claimToken'])
            self.assertEqual(call.args[1]['company'], 'Acme')
            self.assertEqual(call.args[1]['title'], 'SRE')

    def test_running_ai_task_actually_renews_its_evaluation_lease(self) -> None:
        qualification = self.qualify()
        claimed = self.claim(qualification)
        started = self.store.start_ai_evaluation(
            qualification['aiFingerprint'], 'worker-1', lease_seconds=900,
        )

        async def delayed_pass(*_args):
            await asyncio.sleep(0.04)
            return True, 'suitable', True

        with (
            patch.object(delivery_routes, '_AI_RENEW_INTERVAL_SECONDS', 0.01),
            patch.object(
                delivery_routes, 'strict_llm_job_filter', new=AsyncMock(side_effect=delayed_pass),
                create=True,
            ),
            patch.object(
                self.store,
                'renew_ai_evaluation',
                wraps=self.store.renew_ai_evaluation,
            ) as renew,
        ):
            asyncio.run(delivery_routes._run_job_filter_evaluation(
                started['evaluationId'],
                'worker-1',
                qualification['aiFingerprint'],
                self.job(),
                claimed['claimToken'],
            ))
        self.assertGreaterEqual(renew.call_count, 1)

    def test_ai_renew_exception_still_completes_evaluation_and_releases_claim(self) -> None:
        qualification = self.qualify()
        claimed = self.claim(qualification)
        started = self.store.start_ai_evaluation(
            qualification['aiFingerprint'], 'worker-1', lease_seconds=900,
        )

        async def delayed_unreliable(*_args):
            await asyncio.sleep(0.04)
            return False, 'provider failed', False

        with (
            patch.object(delivery_routes, '_AI_RENEW_INTERVAL_SECONDS', 0.01),
            patch.object(
                delivery_routes,
                'strict_llm_job_filter',
                new=AsyncMock(side_effect=delayed_unreliable),
            ),
            patch.object(
                self.store,
                'renew_ai_evaluation',
                side_effect=OSError('sqlite temporarily unavailable'),
            ),
        ):
            asyncio.run(delivery_routes._run_job_filter_evaluation(
                started['evaluationId'],
                'worker-1',
                qualification['aiFingerprint'],
                self.job(),
                claimed['claimToken'],
            ))

        evaluation = self.store.get_ai_evaluation(started['evaluationId'])
        self.assertEqual(evaluation['status'], 'completed')
        self.assertFalse(evaluation['reliable'])
        self.assertFalse(self.store.claim_status(claimed['claimToken'])['exists'])

    def test_ai_context_change_before_completion_is_not_cached(self) -> None:
        qualification = self.qualify()
        claimed = self.claim(qualification)
        started = self.store.start_ai_evaluation(
            qualification['aiFingerprint'], 'worker-1', lease_seconds=900,
        )

        with (
            patch.object(
                delivery_routes,
                'strict_llm_job_filter',
                new=AsyncMock(return_value=(True, 'suitable', True)),
            ),
            patch.object(
                delivery_routes,
                '_build_gate_context',
                return_value={'aiFingerprint': 'changed-context'},
            ),
        ):
            asyncio.run(delivery_routes._run_job_filter_evaluation(
                started['evaluationId'],
                'worker-1',
                qualification['aiFingerprint'],
                self.job(),
                claimed['claimToken'],
            ))

        evaluation = self.store.get_ai_evaluation(started['evaluationId'])
        self.assertEqual(evaluation['reason'], 'ai_context_changed')
        self.assertFalse(evaluation['reliable'])
        self.assertIsNone(self.store.get_ai_decision(qualification['aiFingerprint']))
        self.assertFalse(self.store.claim_status(claimed['claimToken'])['exists'])

    def test_scan_ai_positive_flow_requires_all_three_gates(self) -> None:
        self.policy['safety'].update({'scanOnly': True, 'scanAiEnabled': True})
        qualification = self.qualify(mode='scan')

        async def exercise() -> tuple[dict, dict]:
            started = await delivery_routes.start_job_filter({
                **self.job(mode='scan'),
                'qualificationToken': qualification['qualificationToken'],
                'scanAiEnabled': True,
            })
            status = {'status': 'pending'}
            for _ in range(50):
                if status['status'] != 'pending':
                    break
                await asyncio.sleep(0.005)
                status = await delivery_routes.job_filter_status(started['evaluationId'])
            return started, status

        with (
            patch.object(LLM_MANAGER, '_job_filter', True),
            patch.object(LLM_MANAGER, 'available', return_value=True),
            patch.object(
                delivery_routes,
                'strict_llm_job_filter',
                new=AsyncMock(return_value=(True, 'suitable', True)),
            ),
        ):
            started, status = asyncio.run(exercise())
        self.assertTrue(started['allowed'])
        self.assertEqual(status['status'], 'completed')
        self.assertTrue(status['allowed'])

    def test_scan_ai_start_rechecks_pause_and_active_window(self) -> None:
        self.policy['safety'].update({'scanOnly': True, 'scanAiEnabled': True})
        qualification = self.qualify(mode='scan')
        request = {
            **self.job(mode='scan'),
            'qualificationToken': qualification['qualificationToken'],
            'scanAiEnabled': True,
        }

        self.policy['safety']['globalPaused'] = True
        paused = asyncio.run(delivery_routes.start_job_filter(request))
        self.assertFalse(paused['allowed'])
        self.assertEqual(paused['reason'], 'paused')

        self.policy['safety']['globalPaused'] = False
        self.policy['plan'].update({'activeStart': '00:00', 'activeEnd': '00:01'})
        with patch.object(delivery_routes, '_policy_denial', return_value='outside_active_hours'):
            outside = asyncio.run(delivery_routes.start_job_filter(request))
        self.assertFalse(outside['allowed'])
        self.assertEqual(outside['reason'], 'outside_active_hours')

    def test_shutdown_cancels_and_clears_job_filter_tasks(self) -> None:
        async def exercise() -> tuple[bool, int]:
            started = asyncio.Event()

            async def pending() -> None:
                started.set()
                await asyncio.Event().wait()

            task = asyncio.create_task(pending())
            delivery_routes._register_job_filter_task('eval-shutdown', task)
            await started.wait()
            await delivery_routes.shutdown_introduce_jobs()
            return task.cancelled(), len(delivery_routes._JOB_FILTER_TASKS)

        cancelled, remaining = asyncio.run(exercise())
        self.assertTrue(cancelled)
        self.assertEqual(remaining, 0)

    def test_shutdown_abandons_ai_evaluation_for_immediate_reacquire(self) -> None:
        qualification = self.qualify()
        claimed = self.claim(qualification)
        started = self.store.start_ai_evaluation(
            qualification['aiFingerprint'], 'worker-1', lease_seconds=900,
        )

        async def exercise() -> bool:
            entered = asyncio.Event()

            async def pending_filter(*_args):
                entered.set()
                await asyncio.Event().wait()

            with patch.object(
                delivery_routes,
                'strict_llm_job_filter',
                new=AsyncMock(side_effect=pending_filter),
            ):
                task = asyncio.create_task(delivery_routes._run_job_filter_evaluation(
                    started['evaluationId'],
                    'worker-1',
                    qualification['aiFingerprint'],
                    self.job(),
                    claimed['claimToken'],
                ))
                delivery_routes._register_job_filter_task(started['evaluationId'], task)
                await entered.wait()
                await delivery_routes.shutdown_introduce_jobs()
                return task.cancelled()

        self.assertTrue(asyncio.run(exercise()))
        reacquired = self.store.start_ai_evaluation(
            qualification['aiFingerprint'], 'worker-2', lease_seconds=900,
        )
        self.assertTrue(reacquired['acquired'])


if __name__ == '__main__':
    unittest.main()
