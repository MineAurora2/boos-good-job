"""Browser-client scoring, greeting generation, and delivery coordination routes."""

from __future__ import annotations

import asyncio
from datetime import datetime
import random
import threading
import uuid

from fastapi import APIRouter, Body, HTTPException

from app.state import STATE
from app.config import Config
from app.scoring import evaluate_job_match
from app.llm.manager import LLM_MANAGER
from app.llm.tasks import generate_custom_introduce, llm_job_filter
from app.storage.delivery_store import delivery_key


router = APIRouter()
_INTRODUCE_JOB_LOCK = threading.Lock()
_INTRODUCE_JOBS: dict[str, dict] = {}
_INTRODUCE_JOB_BY_CLAIM: dict[str, str] = {}
_INTRODUCE_JOB_TTL_SECONDS = 15 * 60


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
async def get_job_score(job: str = Body(..., description='职位信息')):
    result = evaluate_job_match(job)
    title = result.get('title') or ''
    salary = result.get('salary') or ''
    detail = result.get('detail') or ''
    delay_ms = max(
        0,
        Config.job_score_delay_base_ms
        + random.randint(-Config.job_score_delay_jitter_ms, Config.job_score_delay_jitter_ms),
    )
    use_ai_filter = LLM_MANAGER.available() and LLM_MANAGER.job_filter_enabled
    (ai_pass, ai_reason), _ = await asyncio.gather(
        llm_job_filter(title, salary, detail),
        asyncio.sleep(delay_ms / 1000),
    )
    if use_ai_filter:
        STATE.record_ai_filter(title, salary, detail, result['score'], ai_pass, ai_reason)
    if use_ai_filter and not ai_pass:
        result['score'] = 0
        result['reason'] = f'AI筛选不通过: {ai_reason}'

    display_title = title or '未识别标题'
    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] /get-job-score | "
        f'title={display_title} | stars={result.get("stars")} | '
        f'deducted={result.get("deductedStars")} | delay_ms={delay_ms} | '
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
    STATE.record_job_decision(result, job, delay_ms)
    return {
        **result,
        'introduce': Config.introduce,
        'introduceGenerated': False,
        'resumeIndex': Config.frontend.get('resumeIndex', 0),
    }


def _validate_introduce_payload(payload: dict) -> dict:
    claim_token = (payload.get('claimToken') or '').strip()
    claim = STATE.delivery_store.claim_status(claim_token)
    if not claim.get('exists'):
        raise HTTPException(status_code=409, detail='投递令牌不存在，禁止提前生成招呼语')
    delivery = claim.get('delivery') or {}
    if delivery.get('status') not in {'reserved', 'queued'}:
        raise HTTPException(status_code=409, detail=f'当前投递状态不允许生成招呼语: {delivery.get("status")}')

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
    # 系统管理关闭 LLM 招呼语后，在这里统一短路同步和后台生成接口。
    if not Config.llm_greeting_enabled:
        return {
            'introduce': Config.introduce,
            'generated': False,
            'fallbackReason': 'LLM 打招呼已关闭',
        }
    print(
        f'[LLM] 所有投递条件已通过，开始生成最终招呼语 | '
        f'company={payload["company"]} | title={payload["title"]}',
        flush=True,
    )
    return await generate_custom_introduce(
        payload['title'],
        payload['salary'],
        payload['detail'],
        return_meta=True,
    )


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
    account_id = payload.get('accountId') or ''
    worker_id = payload.get('workerId') or ''
    company = (payload.get('company') or '').strip()
    title = (payload.get('title') or '').strip()
    existing_status = STATE.delivery_store.company_status(company, title)
    if existing_status.get('exists'):
        existing = existing_status.get('delivery') or {}
        print(
            f'[投递判断] 重复投递，已忽略（不写投递记录、不计投递次数） | '
            f'company={company} | title={title} | existing_account={existing.get("account_id", "")} | '
            f'existing_status={existing.get("status", "")}',
            flush=True,
        )
        return {'accepted': False, 'reason': 'duplicate_job', 'existing': existing}
    quota = STATE.quota_status(account_id)
    if quota['reached']:
        print(
            f'[投递判断] 达到每日上限 | account={account_id} | company={company} | title={title} | '
            f'count={quota.get("count")}/{quota.get("limit")}',
            flush=True,
        )
        return {'accepted': False, 'reason': 'daily_limit', **quota}
    result = STATE.delivery_store.claim(
        company=company,
        title=title,
        account_id=account_id,
        worker_id=worker_id,
        job_url=payload.get('jobUrl') or '',
        daily_limit=quota['limit'],
    )
    if result.get('accepted'):
        result['limit'] = quota['limit']
        result['remaining'] = max(0, quota['limit'] - int(result.get('count') or 0))
        print(
            f'[投递判断] 允许投递 | company={company} | title={title} | '
            f'account={account_id} | count={result.get("count")}/{result.get("limit")}',
            flush=True,
        )
    elif result.get('reason') == 'duplicate_job':
        existing = result.get('existing') or {}
        print(
            f'[投递判断] 重复投递，已忽略（不写投递记录、不计投递次数） | '
            f'company={company} | title={title} | existing_account={existing.get("account_id", "")} | '
            f'existing_status={existing.get("status", "")}',
            flush=True,
        )
    else:
        print(
            f'[投递判断] 拒绝投递 | company={company} | title={title} | reason={result.get("reason")}',
            flush=True,
        )
    return result


@router.post('/delivery/mark', summary='更新投递状态')
def mark_delivery(payload: dict = Body(..., description='领取令牌与投递状态')):
    try:
        return STATE.delivery_store.mark(
            payload.get('claimToken') or '',
            payload.get('status') or '',
            payload.get('error') or '',
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post('/delivery/release', summary='释放尚未发起沟通的投递占位')
def release_delivery(payload: dict = Body(..., description='领取令牌与释放原因')):
    return STATE.delivery_store.release(
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
    return STATE.quota_status(payload.get('accountId') or 'legacy')
