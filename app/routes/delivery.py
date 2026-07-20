"""Browser-client scoring, greeting generation, and delivery coordination routes."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
import threading
from typing import Any
import uuid

from fastapi import APIRouter, Body, HTTPException

from app.state import STATE
from app.config import Config
from app.llm import env_store as llm_env_store, prompts
from app.scoring import evaluate_job_match
from app.llm.manager import LLM_MANAGER
from app.llm.tasks import (
    generate_custom_introduce,
    load_resume,
    strict_llm_job_filter,
)
from app.protocol import SCRIPT_API_VERSION
from app.qualification import QUALIFICATION_TOKENS, build_qualification_context
from app.runtime import RUNTIME_MONITOR
from app.storage.delivery_store import delivery_key


router = APIRouter()
_INTRODUCE_JOB_LOCK = threading.Lock()
_INTRODUCE_JOBS: dict[str, dict] = {}
_INTRODUCE_JOB_BY_CLAIM: dict[str, str] = {}
_INTRODUCE_JOB_TTL_SECONDS = 15 * 60
_JOB_FILTER_TASK_LOCK = threading.Lock()
_JOB_FILTER_TASKS: dict[str, asyncio.Task] = {}
_AI_RENEW_INTERVAL_SECONDS = 60


@router.get('/tags', summary='获取职位标签')
async def get_tags():
    return {'tags': Config.tags}


@router.get('/get-introduce', summary='获取自我介绍')
async def get_introduce():
    return {'introduce': Config.introduce}


@router.get('/client-config', summary='获取前端运行配置')
async def get_client_config():
    return Config.get_client_config()


@router.post('/get-job-score', summary='获取职位匹配度')
async def get_job_score(payload: Any = Body(..., description='V2 职位评分请求')):
    _require_script_v2(payload)
    job = payload.get('job')
    if not isinstance(job, str) or not job.strip():
        raise HTTPException(status_code=422, detail='invalid_job')
    result = evaluate_job_match(job)
    title = result.get('title') or ''

    display_title = title or '未识别标题'
    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] /get-job-score | "
        f'title={display_title} | stars={result.get("stars")} | '
        f'deducted={result.get("deductedStars")} | delay_ms=0 | '
        f'score={result["score"]} | reason={result["reason"]}',
        flush=True,
    )
    for deduction in result.get('deductions') or []:
        print(
            f'[评分扣星] title={display_title} | '
            f'位置={deduction.get("fieldLabel") or deduction.get("field")} | '
            f'关键词={deduction.get("keyword")} | 扣除={deduction.get("deductStars")}星',
            flush=True,
        )
    STATE.record_job_decision(result, job, 0)
    return {
        **result,
        'introduce': Config.introduce,
        'introduceGenerated': False,
        'resumeIndex': Config.frontend.get('resumeIndex', 0),
        'aiFilterEnabled': False,
    }


def _require_script_v2(payload: dict) -> None:
    if not isinstance(payload, dict) or payload.get('scriptApiVersion') != SCRIPT_API_VERSION:
        raise HTTPException(status_code=409, detail='script_api_version_mismatch')


def _denied(reason: str, **details) -> dict:
    return {'success': False, 'allowed': False, 'reason': reason, **details}


def _audit_gate(stage: str, payload: dict, result: dict) -> None:
    RUNTIME_MONITOR.audit(
        f'delivery_gate_{stage}',
        {
            'company': str(payload.get('company') or '')[:200],
            'title': str(payload.get('title') or '')[:200],
            'accountId': str(payload.get('accountId') or '')[:120],
            'workerId': str(payload.get('workerId') or '')[:160],
            'mode': str(payload.get('mode') or ''),
            'allowed': bool(result.get('allowed')),
            'reason': str(result.get('reason') or ''),
            'claimToken': str(
                result.get('claimToken') or payload.get('claimToken') or ''
            ),
            'evaluationId': str(result.get('evaluationId') or ''),
            'aiFingerprint': str(result.get('aiFingerprint') or ''),
        },
        actor='delivery',
    )


def _release_claim(claim_token: str, reason: str) -> dict:
    result = STATE.delivery_store.release(claim_token, reason)
    RUNTIME_MONITOR.audit(
        'delivery_claim_released',
        {
            'claimToken': str(claim_token or ''),
            'company': str(result.get('company') or '')[:200],
            'title': str(result.get('title') or '')[:200],
            'success': bool(result.get('success')),
            'reason': str(result.get('reason') or ''),
        },
        actor='delivery',
    )
    return result


def _audit_greeting(action: str, payload: dict, **details) -> None:
    RUNTIME_MONITOR.audit(
        action,
        {
            'claimToken': str(payload.get('claimToken') or ''),
            'company': str(payload.get('company') or '')[:200],
            'title': str(payload.get('title') or '')[:200],
            **details,
        },
        actor='delivery',
    )


def _in_time_window(value: str, start: str, end: str) -> bool:
    if not start or not end:
        return False
    if start == end:
        return True
    if start < end:
        return start <= value < end
    return value >= start or value < end


def _policy_denial(control: dict, mode: str, *, now_hhmm: str | None = None) -> str:
    safety = control.get('safety') if isinstance(control.get('safety'), dict) else {}
    plan = control.get('plan') if isinstance(control.get('plan'), dict) else {}
    account = control.get('account') if isinstance(control.get('account'), dict) else {}
    policy = control.get('policy') if isinstance(control.get('policy'), dict) else {}
    if control.get('shouldPause') or safety.get('globalPaused') or account.get('paused'):
        return 'paused'
    if safety.get('openingDisabled'):
        return 'opening_disabled'
    if mode == 'delivery':
        if safety.get('scanOnly'):
            return 'scan_only'
        if safety.get('sendingDisabled'):
            return 'sending_disabled'
    elif not safety.get('scanOnly'):
        return 'scan_mode_disabled'
    max_failures = int(plan.get('maxConsecutiveFailures') or 0)
    failures = int(
        control.get('consecutiveFailures')
        or policy.get('consecutiveFailures')
        or 0
    )
    if max_failures > 0 and failures >= max_failures:
        return 'consecutive_failures'
    now_hhmm = now_hhmm or datetime.now().strftime('%H:%M')
    active_start = str(plan.get('activeStart') or '')
    active_end = str(plan.get('activeEnd') or '')
    if active_start and active_end and not _in_time_window(now_hhmm, active_start, active_end):
        return 'outside_active_hours'
    break_start = str(plan.get('breakStart') or '')
    break_end = str(plan.get('breakEnd') or '')
    if break_start and break_end and _in_time_window(now_hhmm, break_start, break_end):
        return 'break_window'
    return ''


def _effective_control(worker_id: str, account_id: str) -> dict:
    control = RUNTIME_MONITOR.effective_control(worker_id, account_id)
    safety = control.get('safety') if isinstance(control.get('safety'), dict) else {}
    plan = control.get('plan') if isinstance(control.get('plan'), dict) else {}
    account = control.get('account') if isinstance(control.get('account'), dict) else {}
    account = {'accountId': account_id, **account}
    return {
        **control,
        'safety': safety,
        'plan': plan,
        'account': account,
        'policy': {**safety, **plan, **account, **(control.get('policy') or {})},
    }


def _quota_settings(control: dict) -> dict:
    plan = control['plan']
    account = control['account']
    raw_daily_limit = account.get('dailyLimit')
    daily_limit = (
        STATE.delivery_store.daily_limit
        if raw_daily_limit is None
        else max(0, int(raw_daily_limit))
    )
    daily_target = int(account.get('dailyTarget') or plan.get('dailyTarget') or 0)
    if not plan.get('stopAtTarget', True):
        daily_target = 0
    return {
        'daily_limit': daily_limit,
        'daily_target': max(0, daily_target),
        'hourly_limit': max(0, int(plan.get('hourlyLimit') or 0)),
        'min_interval_ms': max(0, int(plan.get('minDelayMs') or 0)),
    }


def _scoring_context(control: dict) -> dict:
    del control  # Runtime policy is always rechecked live by qualify/claim/start.
    return {
        'enabled': bool(Config.scoring_enabled),
        'threshold': Config.frontend.get('thread', 0),
        'titleRules': Config.title_deduction_keywords,
        'detailRules': Config.detail_deduction_keywords,
        'hrActiveFilterEnabled': Config.frontend.get('hrActiveFilterEnabled', False),
        'hrActiveLevels': Config.frontend.get('hrActiveLevels', []),
    }


def _safe_llm_route_config() -> dict:
    raw = llm_env_store.load_llm_config()
    return {
        'strategy': raw.get('strategy'),
        'jobFilter': bool(raw.get('jobFilter')),
        'providers': [
            {
                'providerId': item.get('provider_id'),
                'apiBase': item.get('api_base'),
                'model': item.get('model'),
                'enabled': bool(item.get('enabled')),
                'proxyEnabled': bool(item.get('proxy_enabled')),
            }
            for item in raw.get('providers', [])
        ],
    }


def _build_gate_context(payload: dict, control: dict, mode: str) -> dict:
    # Reading local resume/prompt/config is deterministic and intentionally
    # delayed until all cheap rules and quota prechecks have passed.
    return build_qualification_context(
        company=payload.get('company') or '',
        title=payload.get('title') or '',
        salary=payload.get('salary') or '',
        detail=payload.get('detail') or '',
        job_url=payload.get('jobUrl') or '',
        scoring_config=_scoring_context(control),
        resume=load_resume(),
        filter_prompt=prompts.JOB_FILTER,
        llm_config=_safe_llm_route_config(),
        mode=mode,
    )


def _job_rule_text(payload: dict) -> str:
    return (
        f'# 职位名称\n{payload.get("title") or ""}\n'
        f'# 薪资范围\n{payload.get("salary") or ""}\n'
        f'# 职位描述\n{payload.get("detail") or ""}'
    )


@router.post('/delivery/qualify', summary='执行无 LLM 的投递资格门禁')
def qualify_delivery(payload: dict = Body(...)):
    _require_script_v2(payload)
    mode = str(payload.get('mode') or 'delivery').strip().lower()
    if mode not in {'delivery', 'scan'}:
        return _denied('invalid_mode')
    normalized = {
        **payload,
        'mode': mode,
        'company': str(payload.get('company') or '').strip(),
        'title': str(payload.get('title') or '').strip(),
        'salary': str(payload.get('salary') or '').strip(),
        'detail': str(payload.get('detail') or '').strip(),
        'jobUrl': str(payload.get('jobUrl') or '').strip(),
        'accountId': str(payload.get('accountId') or '').strip(),
        'workerId': str(payload.get('workerId') or '').strip(),
    }
    for field, reason in (
        ('company', 'missing_company'),
        ('title', 'missing_title'),
        ('detail', 'missing_detail'),
        ('jobUrl', 'missing_job_url'),
        ('accountId', 'missing_account_id'),
        ('workerId', 'missing_worker_id'),
    ):
        if not normalized[field]:
            result = _denied(reason)
            _audit_gate('fields', normalized, result)
            return result

    duplicate = STATE.delivery_store.company_status(normalized['company'], normalized['title'])
    if duplicate.get('exists'):
        result = _denied('duplicate_job', existing=duplicate.get('delivery'))
        _audit_gate('duplicate', normalized, result)
        return result

    rules = evaluate_job_match(_job_rule_text(normalized))
    threshold = max(0, min(100, int(Config.frontend.get('thread') or 0)))
    if rules.get('discarded') or int(rules.get('score') or 0) < threshold:
        result = {**rules, **_denied('score_below_threshold', threshold=threshold)}
        _audit_gate('rules', normalized, result)
        return result

    hr_level = str(normalized.get('hrActiveLevel') or 'unknown')
    if (
        Config.frontend.get('hrActiveFilterEnabled')
        and hr_level not in set(Config.frontend.get('hrActiveLevels') or [])
    ):
        result = {**rules, **_denied('hr_inactive', hrActiveLevel=hr_level)}
        _audit_gate('hr', normalized, result)
        return result

    control = _effective_control(normalized['workerId'], normalized['accountId'])
    policy_reason = _policy_denial(control, mode)
    if policy_reason:
        result = {**rules, **_denied(policy_reason, policy=control['policy'])}
        _audit_gate('policy', normalized, result)
        return result

    quota = _quota_settings(control)
    if quota['daily_limit'] <= 0:
        result = {**rules, **_denied('daily_limit', count=0, limit=0, remaining=0)}
        _audit_gate('quota', normalized, result)
        return result
    preflight = STATE.delivery_store.preflight(
        company=normalized['company'],
        title=normalized['title'],
        account_id=normalized['accountId'],
        **quota,
    )
    if not preflight.get('allowed'):
        result = {
            **rules,
            **preflight,
            'success': False,
            'allowed': False,
            'reason': preflight.get('reason') or 'quota_rejected',
        }
        _audit_gate('quota', normalized, result)
        return result

    context = _build_gate_context(normalized, control, mode)
    token, expires_at = QUALIFICATION_TOKENS.issue({
        'company': normalized['company'],
        'title': normalized['title'],
        'accountId': normalized['accountId'],
        'workerId': normalized['workerId'],
        'jobFingerprint': context['jobFingerprint'],
        'configFingerprint': context['configFingerprint'],
        'aiFingerprint': context['aiFingerprint'],
        'qualificationFingerprint': context['qualificationFingerprint'],
        'mode': mode,
    })
    result = {
        **rules,
        'ruleReason': rules.get('reason') or '',
        'success': True,
        'allowed': True,
        'reason': 'qualified',
        'qualificationToken': token,
        'qualificationExpiresAt': expires_at,
        **context,
        'policy': control['policy'],
        'resumeIndex': Config.frontend.get('resumeIndex', 0),
        'aiFilterEnabled': bool(LLM_MANAGER.job_filter_enabled),
        'aiFilterAvailable': bool(LLM_MANAGER.available()),
    }
    _audit_gate('qualified', normalized, result)
    return result


def _validate_introduce_payload(payload: dict) -> dict:
    claim_token = (payload.get('claimToken') or '').strip()
    claim = STATE.delivery_store.claim_status(claim_token)
    if not claim.get('exists'):
        raise HTTPException(status_code=409, detail='投递令牌不存在，禁止提前生成招呼语')
    delivery = claim.get('delivery') or {}
    if delivery.get('status') not in {'reserved', 'queued'}:
        raise HTTPException(status_code=409, detail=f'当前投递状态不允许生成招呼语: {delivery.get("status")}')
    if bool(delivery.get('ai_required')):
        decision = STATE.delivery_store.get_ai_decision(delivery.get('ai_fingerprint') or '')
        if not decision or not decision.get('passed'):
            raise HTTPException(status_code=409, detail='AI 岗位筛选尚未可靠通过')

    title = (payload.get('title') or delivery.get('title') or '').strip()
    company = (payload.get('company') or delivery.get('company') or '').strip()
    if delivery_key(company, title) != delivery_key(delivery.get('company') or '', delivery.get('title') or ''):
        raise HTTPException(status_code=409, detail='投递令牌与公司岗位不匹配')
    return {
        'claimToken': claim_token,
        'company': company,
        'title': title,
        'salary': (payload.get('salary') or '').strip(),
        'detail': (payload.get('detail') or '').strip(),
    }


async def _generate_introduce_result(payload: dict) -> dict:
    _audit_greeting('delivery_greeting_started', payload)
    # 系统管理关闭 LLM 招呼语后，在这里统一短路同步和后台生成接口。
    if not Config.llm_greeting_enabled:
        result = {
            'introduce': Config.introduce,
            'generated': False,
            'fallbackReason': 'LLM 打招呼已关闭',
        }
        _audit_greeting(
            'delivery_greeting_fixed_fallback',
            payload,
            reason=result['fallbackReason'],
        )
        _audit_greeting('delivery_greeting_completed', payload, generated=False)
        return result
    print(
        f'[LLM] 所有投递条件已通过，开始生成最终招呼语 | '
        f'company={payload["company"]} | title={payload["title"]}',
        flush=True,
    )
    try:
        result = await generate_custom_introduce(
            payload['title'],
            payload['salary'],
            payload['detail'],
            return_meta=True,
        )
    except Exception as error:
        _audit_greeting('delivery_greeting_failed', payload, reason=str(error)[:1000])
        raise
    if not result.get('generated'):
        _audit_greeting(
            'delivery_greeting_fixed_fallback',
            payload,
            reason=str(result.get('fallbackReason') or '')[:1000],
        )
    _audit_greeting(
        'delivery_greeting_completed',
        payload,
        generated=bool(result.get('generated')),
    )
    return result


def _prune_introduce_jobs(now: float) -> None:
    expired = [
        job_id
        for job_id, job in _INTRODUCE_JOBS.items()
        if job['status'] != 'pending' and now - job['updatedAt'] >= _INTRODUCE_JOB_TTL_SECONDS
    ]
    for job_id in expired:
        job = _INTRODUCE_JOBS.pop(job_id)
        if _INTRODUCE_JOB_BY_CLAIM.get(job['claimToken']) == job_id:
            _INTRODUCE_JOB_BY_CLAIM.pop(job['claimToken'], None)


def _introduce_job_response(job_id: str, job: dict) -> dict:
    response = {'jobId': job_id, 'status': job['status']}
    if job['status'] == 'completed':
        response.update(job['result'])
    elif job['status'] == 'failed':
        response['error'] = job['error']
    return response


async def _run_introduce_job(job_id: str, payload: dict) -> None:
    try:
        result = await _generate_introduce_result(payload)
        status = 'completed'
        error = ''
    except asyncio.CancelledError:
        with _INTRODUCE_JOB_LOCK:
            job = _INTRODUCE_JOBS.pop(job_id, None)
            if job and _INTRODUCE_JOB_BY_CLAIM.get(job['claimToken']) == job_id:
                _INTRODUCE_JOB_BY_CLAIM.pop(job['claimToken'], None)
        raise
    except Exception as exc:
        result = None
        status = 'failed'
        error = str(exc)
        print(f'[LLM] 后台招呼语任务失败 | job={job_id} | error={error}', flush=True)
    with _INTRODUCE_JOB_LOCK:
        job = _INTRODUCE_JOBS.get(job_id)
        if job is not None:
            job.update(
                status=status,
                result=result,
                error=error,
                updatedAt=asyncio.get_running_loop().time(),
                task=None,
            )


async def shutdown_introduce_jobs() -> None:
    """Cancel pending greeting tasks and clear process-local task indexes on shutdown."""
    with _INTRODUCE_JOB_LOCK:
        tasks = [
            job.get('task')
            for job in _INTRODUCE_JOBS.values()
            if isinstance(job.get('task'), asyncio.Task) and not job['task'].done()
        ]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    with _INTRODUCE_JOB_LOCK:
        _INTRODUCE_JOBS.clear()
        _INTRODUCE_JOB_BY_CLAIM.clear()
    with _JOB_FILTER_TASK_LOCK:
        filter_tasks = [task for task in _JOB_FILTER_TASKS.values() if not task.done()]
    for task in filter_tasks:
        task.cancel()
    if filter_tasks:
        await asyncio.gather(*filter_tasks, return_exceptions=True)
    with _JOB_FILTER_TASK_LOCK:
        _JOB_FILTER_TASKS.clear()


@router.post('/generate-introduce', summary='在投递条件全部通过后生成最终招呼语')
async def generate_introduce(payload: dict = Body(..., description='岗位信息与已领取的投递令牌')):
    return await _generate_introduce_result(_validate_introduce_payload(payload))


@router.post('/generate-introduce/start', summary='启动后台招呼语生成任务')
async def start_generate_introduce(payload: dict = Body(..., description='岗位信息与已领取的投递令牌')):
    normalized = _validate_introduce_payload(payload)
    loop = asyncio.get_running_loop()
    now = loop.time()
    with _INTRODUCE_JOB_LOCK:
        _prune_introduce_jobs(now)
        existing_id = _INTRODUCE_JOB_BY_CLAIM.get(normalized['claimToken'])
        existing = _INTRODUCE_JOBS.get(existing_id) if existing_id else None
        if existing is not None:
            return _introduce_job_response(existing_id, existing)
        job_id = uuid.uuid4().hex
        job = {
            'claimToken': normalized['claimToken'],
            'status': 'pending',
            'result': None,
            'error': '',
            'updatedAt': now,
            'task': None,
        }
        _INTRODUCE_JOBS[job_id] = job
        _INTRODUCE_JOB_BY_CLAIM[normalized['claimToken']] = job_id
        job['task'] = loop.create_task(_run_introduce_job(job_id, normalized))
        return _introduce_job_response(job_id, job)


@router.get('/generate-introduce/status/{job_id}', summary='查询后台招呼语生成结果')
async def generate_introduce_status(job_id: str):
    with _INTRODUCE_JOB_LOCK:
        _prune_introduce_jobs(asyncio.get_running_loop().time())
        job = _INTRODUCE_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail='招呼语生成任务不存在或已过期')
        return _introduce_job_response(job_id, job)


@router.post('/log-action', summary='记录前端动作日志')
async def log_action(action: dict = Body(..., description='动作日志')):
    STATE.record_job_action(action)
    return {'success': True}


@router.post('/delivery/claim', summary='原子领取公司与岗位投递权')
def claim_delivery(payload: dict = Body(..., description='公司、岗位、账号与浏览器实例信息')):
    _require_script_v2(payload)
    account_id = str(payload.get('accountId') or '').strip()
    worker_id = str(payload.get('workerId') or '').strip()
    company = (payload.get('company') or '').strip()
    title = (payload.get('title') or '').strip()
    qualification_token = str(payload.get('qualificationToken') or '').strip()
    control = _effective_control(worker_id, account_id)
    current_config = build_qualification_context(
        company='',
        title='',
        scoring_config=_scoring_context(control),
        resume='',
        filter_prompt='',
        llm_config={},
        mode='delivery',
    )['configFingerprint']
    try:
        claims = QUALIFICATION_TOKENS.verify(
            qualification_token,
            expected={
                'company': company,
                'title': title,
                'accountId': account_id,
                'workerId': worker_id,
                'configFingerprint': current_config,
                'mode': 'delivery',
            },
        )
    except ValueError as error:
        raw_reason = str(error).split(':', 1)[0]
        reason = 'qualification_mismatch' if raw_reason == 'qualification_mismatch' else raw_reason
        result = {**_denied(reason), 'accepted': False}
        _audit_gate('claim', payload, result)
        return result
    qualification_fingerprint = str(claims.get('qualificationFingerprint') or '')
    if not qualification_fingerprint:
        result = {**_denied('invalid_qualification_token'), 'accepted': False}
        _audit_gate('claim', payload, result)
        return result

    policy_reason = _policy_denial(control, 'delivery')
    quota = _quota_settings(control)
    if quota['daily_limit'] <= 0:
        result = {
            **_denied('daily_limit', count=0, limit=0, remaining=0),
            'accepted': False,
        }
        _audit_gate('claim', payload, result)
        return result

    def refresh_policy() -> dict:
        latest_control = _effective_control(worker_id, account_id)
        return {
            **_quota_settings(latest_control),
            'policy_reason': _policy_denial(latest_control, 'delivery'),
        }

    result = STATE.delivery_store.claim(
        company=company,
        title=title,
        account_id=account_id,
        worker_id=worker_id,
        job_url=payload.get('jobUrl') or '',
        qualification_fingerprint=qualification_fingerprint,
        ai_fingerprint=claims.get('aiFingerprint') or '',
        ai_required=bool(LLM_MANAGER.job_filter_enabled),
        policy_reason=policy_reason,
        policy_refresh=refresh_policy,
        **quota,
    )
    result.setdefault('success', bool(result.get('accepted')))
    result.setdefault('allowed', bool(result.get('accepted')))
    _audit_gate('claim', payload, result)
    return result


@router.post('/delivery/renew', summary='续租尚未开始平台副作用的投递权')
def renew_delivery(payload: dict = Body(...)):
    _require_script_v2(payload)
    result = STATE.delivery_store.renew(
        payload.get('claimToken') or '',
        worker_id=payload.get('workerId') or '',
    )
    RUNTIME_MONITOR.audit(
        'delivery_gate_renew',
        {
            'workerId': payload.get('workerId'),
            'success': bool(result.get('success')),
            'reason': result.get('reason'),
        },
        actor='delivery',
    )
    return result


def _qualification_error(error: ValueError) -> str:
    reason = str(error).split(':', 1)[0]
    return 'qualification_mismatch' if reason == 'qualification_mismatch' else reason


async def _renew_ai_evaluation_lease(evaluation_id: str, owner_id: str) -> None:
    while True:
        await asyncio.sleep(_AI_RENEW_INTERVAL_SECONDS)
        result = await asyncio.to_thread(
            STATE.delivery_store.renew_ai_evaluation,
            evaluation_id,
            owner_id,
        )
        if not result.get('success'):
            return


async def _run_job_filter_evaluation(
    evaluation_id: str,
    owner_id: str,
    ai_fingerprint: str,
    payload: dict,
    claim_token: str = '',
) -> None:
    renew_task = asyncio.create_task(
        _renew_ai_evaluation_lease(evaluation_id, owner_id),
    )
    try:
        passed, reason, reliable = await strict_llm_job_filter(
            str(payload.get('title') or ''),
            str(payload.get('salary') or ''),
            str(payload.get('detail') or ''),
        )
        latest_control = _effective_control(
            str(payload.get('workerId') or owner_id),
            str(payload.get('accountId') or ''),
        )
        latest_context = _build_gate_context(
            payload,
            latest_control,
            str(payload.get('mode') or 'delivery'),
        )
        if latest_context.get('aiFingerprint') != ai_fingerprint:
            passed, reason, reliable = False, 'ai_context_changed', False
    except asyncio.CancelledError:
        try:
            STATE.delivery_store.abandon_ai_evaluation(evaluation_id, owner_id)
        except Exception:
            pass
        if claim_token:
            _release_claim(claim_token, 'ai_evaluation_cancelled')
        raise
    except Exception as error:
        passed, reason, reliable = False, f'ai_transport_failure: {str(error)[:200]}', False
    finally:
        renew_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await renew_task

    completed = STATE.delivery_store.complete_ai_evaluation(
        evaluation_id,
        passed=passed,
        reason=reason,
        reliable=reliable,
    )
    if not completed.get('success'):
        passed = False
        reliable = False
        reason = completed.get('reason') or 'evaluation_completion_failed'
    if claim_token and (not reliable or not passed):
        _release_claim(
            claim_token,
            'ai_rejected' if reliable else 'ai_unreliable',
        )
    try:
        STATE.record_ai_filter(
            str(payload.get('title') or ''),
            str(payload.get('salary') or ''),
            str(payload.get('detail') or ''),
            int(payload.get('score') or 0),
            bool(passed),
            reason,
        )
    except Exception:
        pass
    RUNTIME_MONITOR.audit(
        'delivery_gate_ai_completed',
        {
            'evaluationId': evaluation_id,
            'aiFingerprint': ai_fingerprint,
            'claimToken': claim_token,
            'company': str(payload.get('company') or '')[:200],
            'title': str(payload.get('title') or '')[:200],
            'accountId': str(payload.get('accountId') or '')[:120],
            'workerId': str(payload.get('workerId') or owner_id)[:160],
            'passed': bool(passed),
            'reliable': bool(reliable),
            'reason': reason,
            'claimReleased': bool(claim_token and (not reliable or not passed)),
        },
        actor='delivery',
    )


def _register_job_filter_task(evaluation_id: str, task: asyncio.Task) -> None:
    with _JOB_FILTER_TASK_LOCK:
        _JOB_FILTER_TASKS[evaluation_id] = task

    def cleanup(completed: asyncio.Task) -> None:
        with _JOB_FILTER_TASK_LOCK:
            if _JOB_FILTER_TASKS.get(evaluation_id) is completed:
                _JOB_FILTER_TASKS.pop(evaluation_id, None)

    task.add_done_callback(cleanup)


@router.post('/job-filter/start', summary='资格门禁后启动 AI 岗位筛选')
async def start_job_filter(payload: dict = Body(...)):
    _require_script_v2(payload)
    mode = str(payload.get('mode') or 'delivery').strip().lower()
    if mode not in {'delivery', 'scan'}:
        return _denied('invalid_mode')
    qualification_token = str(payload.get('qualificationToken') or '').strip()
    try:
        unsigned_expected = QUALIFICATION_TOKENS.verify(qualification_token)
    except ValueError as error:
        return _denied(_qualification_error(error))

    normalized = {
        **payload,
        'mode': mode,
        'company': str(payload.get('company') or unsigned_expected.get('company') or '').strip(),
        'title': str(payload.get('title') or '').strip(),
        'salary': str(payload.get('salary') or '').strip(),
        'detail': str(payload.get('detail') or '').strip(),
        'jobUrl': str(payload.get('jobUrl') or '').strip(),
        'accountId': str(payload.get('accountId') or unsigned_expected.get('accountId') or '').strip(),
        'workerId': str(payload.get('workerId') or unsigned_expected.get('workerId') or '').strip(),
    }
    for field, reason in (
        ('company', 'missing_company'),
        ('title', 'missing_title'),
        ('detail', 'missing_detail'),
        ('jobUrl', 'missing_job_url'),
        ('accountId', 'missing_account_id'),
        ('workerId', 'missing_worker_id'),
    ):
        if not normalized[field]:
            return _denied(reason)

    claim_token = str(payload.get('claimToken') or '').strip()
    claim_delivery_state = None
    if mode == 'delivery':
        if not claim_token:
            return _denied('claim_required')
        claim = STATE.delivery_store.claim_status(claim_token)
        if not claim.get('exists'):
            return _denied('claim_required')
        claim_delivery_state = claim.get('delivery') or {}
        if claim_delivery_state.get('status') != 'reserved':
            return _denied('claim_not_reserved', status=claim_delivery_state.get('status'))
        if (
            delivery_key(normalized['company'], normalized['title'])
            != delivery_key(
                claim_delivery_state.get('company') or '',
                claim_delivery_state.get('title') or '',
            )
            or normalized['accountId'] != claim_delivery_state.get('account_id')
            or normalized['workerId'] != claim_delivery_state.get('worker_id')
        ):
            return _denied('claim_mismatch')

    control = _effective_control(normalized['workerId'], normalized['accountId'])
    if mode == 'scan':
        safety = control['safety']
        if (
            payload.get('scanAiEnabled') is not True
            or not safety.get('scanOnly')
            or not safety.get('scanAiEnabled')
        ):
            return _denied('scan_ai_disabled')
        policy_reason = _policy_denial(control, mode)
        if policy_reason:
            return _denied(policy_reason)
    else:
        policy_reason = _policy_denial(control, mode)
        if policy_reason:
            _release_claim(claim_token, policy_reason)
            return _denied(policy_reason)

    context = _build_gate_context(normalized, control, mode)
    try:
        claims = QUALIFICATION_TOKENS.verify(
            qualification_token,
            expected={
                'company': normalized['company'],
                'title': normalized['title'],
                'accountId': normalized['accountId'],
                'workerId': normalized['workerId'],
                'jobFingerprint': context['jobFingerprint'],
                'configFingerprint': context['configFingerprint'],
                'mode': mode,
            },
        )
    except ValueError as error:
        if claim_token:
            _release_claim(claim_token, 'qualification_invalid_before_ai')
        return _denied(_qualification_error(error))

    claim_requires_ai = bool(
        claim_delivery_state is None or claim_delivery_state.get('ai_required')
    )
    if mode == 'delivery' and not claim_requires_ai:
        result = {
            'success': True,
            'allowed': True,
            'reason': 'ai_disabled',
            'status': 'completed',
            'cached': False,
            'passed': True,
            'aiReason': '领取投递权时未要求AI筛选',
            'reliable': True,
            'aiFingerprint': context['aiFingerprint'],
        }
        _audit_gate('ai_skipped', normalized, result)
        return result
    if not LLM_MANAGER.job_filter_enabled:
        if claim_token:
            skipped = STATE.delivery_store.skip_ai_requirement(claim_token)
            if not skipped.get('success'):
                return _denied(skipped.get('reason') or 'ai_skip_failed')
        result = {
            'success': True,
            'allowed': True,
            'reason': 'ai_disabled',
            'status': 'completed',
            'cached': False,
            'passed': True,
            'aiReason': '未启用AI筛选',
            'reliable': True,
            'aiFingerprint': context['aiFingerprint'],
        }
        _audit_gate('ai_skipped', normalized, result)
        return result

    if (
        claims.get('aiFingerprint') != context['aiFingerprint']
        or claims.get('qualificationFingerprint') != context['qualificationFingerprint']
    ):
        if claim_token:
            _release_claim(claim_token, 'qualification_context_changed')
        return _denied('qualification_mismatch')
    if (
        claim_delivery_state is not None
        and claim_delivery_state.get('qualification_fingerprint')
        != context['qualificationFingerprint']
    ):
        _release_claim(claim_token, 'claim_qualification_mismatch')
        return _denied('claim_qualification_mismatch')
    if (
        claim_delivery_state is not None
        and claim_delivery_state.get('ai_fingerprint')
        and claim_delivery_state.get('ai_fingerprint') != context['aiFingerprint']
    ):
        _release_claim(claim_token, 'claim_ai_fingerprint_mismatch')
        return _denied('claim_ai_fingerprint_mismatch')

    if not LLM_MANAGER.available():
        if claim_token:
            _release_claim(claim_token, 'ai_unavailable')
        result = {
            **_denied('ai_unavailable'),
            'status': 'completed',
            'cached': False,
            'passed': False,
            'aiReason': 'ai_unavailable',
            'reliable': False,
            'aiFingerprint': context['aiFingerprint'],
        }
        _audit_gate('ai_unavailable', normalized, result)
        return result

    started = STATE.delivery_store.start_ai_evaluation(
        context['aiFingerprint'],
        normalized['workerId'],
        lease_scope=(
            f'delivery:{claim_token}' if mode == 'delivery' else 'scan'
        ),
    )
    if started.get('cached'):
        passed = bool(started.get('passed'))
        if claim_token and not passed:
            _release_claim(claim_token, 'ai_cached_reject')
        result = {
            'success': True,
            'allowed': passed,
            'reason': 'cached',
            'status': 'completed',
            'cached': True,
            'passed': passed,
            'aiReason': started.get('reason') or '',
            'reliable': True,
            'aiFingerprint': context['aiFingerprint'],
        }
        _audit_gate('ai_cache', normalized, result)
        return result
    evaluation_id = started['evaluationId']
    if started.get('acquired'):
        task = asyncio.create_task(
            _run_job_filter_evaluation(
                evaluation_id,
                normalized['workerId'],
                context['aiFingerprint'],
                {**normalized, 'score': payload.get('score')},
                claim_token,
            )
        )
        _register_job_filter_task(evaluation_id, task)
    result = {
        'success': True,
        'allowed': True,
        'reason': 'evaluation_started' if started.get('acquired') else 'evaluation_pending',
        'evaluationId': evaluation_id,
        'status': 'pending',
        'cached': False,
        'leaseExpiresAt': started.get('leaseExpiresAt'),
        'aiFingerprint': context['aiFingerprint'],
    }
    _audit_gate('ai_start', normalized, result)
    return result


@router.get('/job-filter/status/{evaluation_id}', summary='查询 AI 岗位筛选状态')
async def job_filter_status(evaluation_id: str):
    evaluation = STATE.delivery_store.get_ai_evaluation(evaluation_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail='evaluation_not_found')
    status = evaluation['status']
    if status == 'pending':
        return {
            **evaluation,
            'success': True,
            'allowed': True,
            'reason': 'evaluating',
        }
    if status == 'expired':
        return {
            'success': False,
            'allowed': False,
            'reason': 'evaluation_lease_expired',
            **evaluation,
        }
    passed = bool(evaluation.get('passed'))
    reliable = bool(evaluation.get('reliable'))
    reason = 'ai_passed' if reliable and passed else ('ai_rejected' if reliable else 'ai_unreliable')
    return {
        'success': True,
        'allowed': bool(reliable and passed),
        **evaluation,
        'reason': reason,
        'aiReason': evaluation.get('reason') or '',
    }


@router.post('/delivery/mark', summary='更新投递状态')
def mark_delivery(payload: dict = Body(..., description='领取令牌与投递状态')):
    claim_token = payload.get('claimToken') or ''
    status = payload.get('status') or ''

    def check_policy(delivery: dict) -> str:
        control = _effective_control(
            str(delivery.get('worker_id') or ''),
            str(delivery.get('account_id') or ''),
        )
        return _policy_denial(control, 'delivery')

    try:
        result = STATE.delivery_store.mark(
            claim_token,
            status,
            payload.get('error') or '',
            policy_check=check_policy if status == 'queued' else None,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    RUNTIME_MONITOR.audit(
        f'delivery_state_{status}',
        {
            'claimToken': str(claim_token),
            'company': str(result.get('company') or '')[:200],
            'title': str(result.get('title') or '')[:200],
            'success': bool(result.get('success')),
            'reason': str(result.get('reason') or ''),
            'idempotent': bool(result.get('idempotent')),
        },
        actor='delivery',
    )
    return result


@router.post('/delivery/release', summary='释放尚未发起沟通的投递占位')
def release_delivery(payload: dict = Body(..., description='领取令牌与释放原因')):
    return _release_claim(
        payload.get('claimToken') or '',
        payload.get('reason') or '',
    )


@router.post('/check-greet', summary='检查公司与岗位是否已领取或投递（兼容旧客户端）')
def check_greet(payload: dict = Body(..., description='公司名和职位名')):
    company = (payload.get('company') or '').strip()
    title = (payload.get('title') or '').strip()
    status = STATE.delivery_store.company_status(company, title)
    if status.get('exists'):
        delivery = status.get('delivery') or {}
        print(
            f'[投递预检] 重复投递，LLM 前置跳过 | company={company} | title={title} | '
            f'existing_account={delivery.get("account_id", "")} | '
            f'existing_status={delivery.get("status", "")}',
            flush=True,
        )
    return {
        'greeted': status['exists'],
        'company': company,
        'title': title,
        'delivery': status.get('delivery'),
    }


@router.post('/log-greet', summary='记录已打招呼的岗位（兼容旧客户端）')
def log_greet(payload: dict = Body(..., description='公司名和职位名')):
    return STATE.delivery_store.record_legacy_sent(
        payload.get('company') or '',
        payload.get('title') or '',
        payload.get('accountId') or 'legacy',
    )


@router.post('/increment-daily-greet', summary='增加每日打招呼计数（兼容旧客户端）')
def increment_daily_greet(payload: dict = Body(default={})):
    return STATE.delivery_store.increment_usage(payload.get('accountId') or 'legacy')


@router.post('/check-daily-limit', summary='检查账号每日打招呼次数限制')
def check_daily_limit(payload: dict = Body(default={})):
    account_id = str(payload.get('accountId') or 'legacy').strip() or 'legacy'
    worker_id = str(payload.get('workerId') or '').strip()
    control = _effective_control(worker_id, account_id)
    quota = _quota_settings(control)
    result = STATE.delivery_store.policy_status(account_id, **quota)
    policy_reason = _policy_denial(control, 'delivery')
    if policy_reason:
        result.update({
            'allowed': False,
            'reason': policy_reason,
            'reached': True,
        })
    result['policy'] = control['policy']
    _audit_gate('startup', payload, result)
    return result
