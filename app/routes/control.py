"""Dashboard, administration, runtime monitoring, and diagnostics routes."""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime
import json
import sqlite3

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from starlette.requests import Request

from app.state import STATE, require_local_admin
from app.config import Config
from app.runtime import RUNTIME_MONITOR
from app.llm.env_store import public_llm_config, save_llm_config
from app.llm.manager import LLM_MANAGER
from app.storage.admin_store import (
    get_prompts,
    get_public_config,
    list_resumes,
    read_resume,
    save_config,
    save_prompts,
    save_resume,
    select_resume,
)
from app.storage.dashboard_data import delivery_sources, load_dashboard_data
from app.storage.delivery_store import delivery_key
from app.storage.io import read_jsonl, replace_jsonl


router = APIRouter()


@router.get('/dashboard', include_in_schema=False)
async def dashboard_page():
    return FileResponse(STATE.dashboard_dir / 'index.html')


@router.get('/dashboard/styles.css', include_in_schema=False)
async def dashboard_styles():
    return FileResponse(STATE.dashboard_dir / 'styles.css', media_type='text/css')


@router.get('/dashboard/app.js', include_in_schema=False)
async def dashboard_script():
    return FileResponse(STATE.dashboard_dir / 'app.js', media_type='application/javascript')


@router.get('/dashboard/china.json', include_in_schema=False)
async def dashboard_china_map():
    return FileResponse(STATE.dashboard_dir / 'china.json', media_type='application/json')


@router.get('/dashboard/china-cities.json', include_in_schema=False)
async def dashboard_china_city_map():
    return FileResponse(STATE.dashboard_dir / 'china-cities.json', media_type='application/json')


@router.get('/api/dashboard', summary='获取投递统计面板数据')
def get_dashboard_data():
    with STATE.action_log_lock:
        return load_dashboard_data(STATE.action_log_path, STATE.delivery_store)


@router.post('/api/admin/deliveries/delete', summary='删除单条或批量投递记录')
def admin_delete_deliveries(request: Request, payload: dict = Body(...)):
    require_local_admin(request)
    record_ids = {str(value).strip() for value in (payload.get('ids') or []) if str(value).strip()}
    if not record_ids:
        raise HTTPException(status_code=400, detail='请选择需要删除的投递记录')
    if len(record_ids) > 500:
        raise HTTPException(status_code=400, detail='单次最多删除 500 条记录')

    with STATE.action_log_lock:
        actions = read_jsonl(STATE.action_log_path)
        source_map = {source['id']: source for source in delivery_sources(actions)}
        selected = [source_map[record_id] for record_id in record_ids if record_id in source_map]
        if not selected:
            raise HTTPException(status_code=404, detail='投递记录已不存在，请刷新页面')

        selected_indices = {source['index'] for source in selected}
        claim_tokens = set()
        jobs = set()
        for source in selected:
            record = source['record']
            if record.get('action') == 'company_duplicate_skipped':
                continue
            token = (record.get('claimToken') or '').strip()
            if token:
                claim_tokens.add(token)
            company = (record.get('company') or '').strip()
            title = (record.get('title') or '').strip()
            if company and title:
                jobs.add((company, title))

        job_keys = {delivery_key(company, title) for company, title in jobs}
        for index, record in enumerate(actions):
            if (record.get('claimToken') or '').strip() in claim_tokens:
                selected_indices.add(index)
                continue
            key = delivery_key(record.get('company') or '', record.get('title') or '')
            if key and key in job_keys:
                selected_indices.add(index)

        try:
            database_result = STATE.delivery_store.delete_history(claim_tokens, jobs)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        replace_jsonl(
            STATE.action_log_path,
            (record for index, record in enumerate(actions) if index not in selected_indices),
        )

    if jobs and STATE.greeted_log_path.exists():
        with STATE.greeted_log_lock:
            greeted = read_jsonl(STATE.greeted_log_path)
            replace_jsonl(
                STATE.greeted_log_path,
                (
                    record
                    for record in greeted
                    if delivery_key(record.get('company') or '', record.get('title') or '') not in job_keys
                ),
            )

    RUNTIME_MONITOR.audit('delivery_records_deleted', {
        'visibleRecords': len(selected),
        'actionRows': len(selected_indices),
        'databaseRows': database_result.get('deleted', 0),
    })
    return {
        'success': True,
        'deleted': len(selected),
        'deletedActionRows': len(selected_indices),
        'deletedDatabaseRows': database_result.get('deleted', 0),
    }


@router.post('/api/runtime/heartbeat', summary='脚本运行心跳与日志批量上报')
def runtime_heartbeat(payload: dict = Body(...)):
    try:
        return RUNTIME_MONITOR.heartbeat(payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get('/api/runtime', summary='获取脚本接入与运行状态')
def runtime_snapshot():
    return {
        **RUNTIME_MONITOR.snapshot(),
        'events': RUNTIME_MONITOR.recent_events(120),
        'llm': LLM_MANAGER.snapshot(),
    }


@router.get('/api/control/state', summary='获取控制中心完整状态')
def control_state(request: Request):
    require_local_admin(request)
    state = RUNTIME_MONITOR.control_state()
    account_ids = {
        client.get('accountId') for client in state['clients'] if client.get('accountId')
    } | set(state['accounts'])
    state['quotas'] = {account_id: STATE.quota_status(account_id) for account_id in account_ids}
    return state


@router.put('/api/control/accounts/{account_id}', summary='更新账号别名、配额与运行策略')
def control_update_account(account_id: str, request: Request, payload: dict = Body(...)):
    require_local_admin(request)
    try:
        safe_payload = {key: value for key, value in payload.items() if key in {'alias', 'dailyLimit', 'notes'}}
        policy = RUNTIME_MONITOR.update_account(account_id, safe_payload)
        return {'policy': policy, 'quota': STATE.quota_status(account_id)}
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.put('/api/control/errors/{error_id}', summary='处理或重新打开异常')
def control_resolve_error(error_id: str, request: Request, payload: dict = Body(default={})):
    require_local_admin(request)
    try:
        return RUNTIME_MONITOR.resolve_error(error_id, bool(payload.get('resolved', True)))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get('/api/control/health', summary='检查控制中心、数据库与配置健康状态')
def control_health(request: Request):
    require_local_admin(request)
    llm_state = LLM_MANAGER.snapshot()
    checks = {
        'runtime': {'ok': True},
        'config': {'ok': True},
        'database': {'ok': False},
        'llm': {
            'ok': not llm_state['enabled'] or any(p.get('circuit') != 'open' for p in llm_state['providers']),
            **llm_state,
        },
    }
    database_path = STATE.delivery_db_path
    try:
        with closing(sqlite3.connect(database_path, timeout=2)) as connection:
            connection.execute('SELECT 1').fetchone()
        checks['database'] = {
            'ok': True,
            'path': str(database_path),
            'sizeBytes': database_path.stat().st_size,
        }
    except Exception as error:
        checks['database'] = {'ok': False, 'error': str(error)}
    return {
        'ok': all(check['ok'] for check in checks.values()),
        'checkedAt': datetime.now().isoformat(timespec='seconds'),
        'checks': checks,
        **RUNTIME_MONITOR.diagnostics(),
    }


@router.get('/api/control/diagnostics', summary='下载式控制中心诊断信息')
def control_diagnostics(request: Request):
    require_local_admin(request)
    database_path = STATE.delivery_db_path
    return {
        'generatedAt': datetime.now().isoformat(timespec='seconds'),
        'runtime': RUNTIME_MONITOR.diagnostics(),
        'state': RUNTIME_MONITOR.control_state(300),
        'files': {
            path.name: {'exists': path.exists(), 'sizeBytes': path.stat().st_size if path.exists() else 0}
            for path in (
                STATE.decision_log_path,
                STATE.action_log_path,
                STATE.ai_filter_log_path,
                database_path,
            )
        },
        'config': {
            'tags': len(Config.tags),
            'llmEnabled': LLM_MANAGER.available(),
            'deliveryDatabase': str(database_path),
        },
        'llm': LLM_MANAGER.snapshot(),
    }


@router.post('/api/control/reload-config', summary='重新加载运行配置')
def control_reload_config(request: Request):
    require_local_admin(request)
    Config.reload()
    LLM_MANAGER.reload()
    LLM_MANAGER.reset_circuits(clear_cache=True)
    STATE.delivery_store.daily_limit = max(
        1,
        int(Config.backend.get('daily_greet_limit', STATE.delivery_store.daily_limit)),
    )
    RUNTIME_MONITOR.audit('config_reloaded', {})
    RUNTIME_MONITOR.publish('config_updated', {'restartRequired': []})
    return {'success': True, 'reloadedAt': datetime.now().isoformat(timespec='seconds')}


@router.get('/api/runtime/events', include_in_schema=False)
async def runtime_events(request: Request, cursor: int = 0):
    async def stream():
        current = max(0, cursor)
        keep_alive_at = asyncio.get_running_loop().time()
        try:
            while not await request.is_disconnected():
                events = RUNTIME_MONITOR.events_after(current, 0)
                if events:
                    for event in events:
                        current = event['id']
                        yield (
                            f"id: {event['id']}\n"
                            f"event: {event['type']}\n"
                            f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        )
                    keep_alive_at = asyncio.get_running_loop().time()
                    continue
                now = asyncio.get_running_loop().time()
                if now - keep_alive_at >= 15:
                    yield ': keep-alive\n\n'
                    keep_alive_at = now
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            return

    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})


@router.get('/api/admin/config', summary='读取网页管理配置')
def admin_get_config(request: Request):
    require_local_admin(request)
    return get_public_config()


@router.put('/api/admin/config', summary='保存网页管理配置')
def admin_save_config(request: Request, payload: dict = Body(...)):
    require_local_admin(request)
    try:
        result = save_config(payload)
        LLM_MANAGER.reset_circuits(clear_cache=True)
        STATE.delivery_store.daily_limit = max(
            1,
            int(Config.backend.get('daily_greet_limit', STATE.delivery_store.daily_limit)),
        )
        RUNTIME_MONITOR.publish('config_updated', {'restartRequired': result['restartRequiredFields']})
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get('/api/admin/llm', summary='读取大模型接口配置')
def admin_get_llm(request: Request):
    require_local_admin(request)
    return public_llm_config()


@router.put('/api/admin/llm', summary='保存大模型接口配置到 .env')
def admin_save_llm(request: Request, payload: dict = Body(...)):
    require_local_admin(request)
    try:
        result = save_llm_config(payload)
        LLM_MANAGER.reload()
        RUNTIME_MONITOR.publish('llm_updated', {'providerCount': len(result['providers'])})
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post('/api/admin/llm/test', summary='测试大模型接口连通性')
async def admin_test_llm(request: Request, payload: dict = Body(default={})):
    require_local_admin(request)
    provider = payload.get('provider') if isinstance(payload, dict) else None
    if isinstance(provider, dict):
        return await LLM_MANAGER.test_provider_payload(provider)
    index = payload.get('index') if isinstance(payload, dict) else None
    if isinstance(index, int):
        return await LLM_MANAGER.test_provider(index)
    return {'results': await LLM_MANAGER.test_all()}


@router.get('/api/admin/resumes', summary='列出可管理简历')
def admin_list_resumes(request: Request):
    require_local_admin(request)
    return list_resumes()


@router.put('/api/admin/resumes/current', summary='设置 LLM 当前使用的简历')
def admin_select_resume(request: Request, payload: dict = Body(...)):
    require_local_admin(request)
    try:
        result = select_resume(str(payload.get('name') or ''))
        RUNTIME_MONITOR.publish('resume_selected', {'name': result['selected']})
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get('/api/admin/resumes/{name}', summary='读取简历')
def admin_read_resume(name: str, request: Request):
    require_local_admin(request)
    try:
        return read_resume(name)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.put('/api/admin/resumes/{name}', summary='保存简历')
def admin_save_resume(name: str, request: Request, payload: dict = Body(...)):
    require_local_admin(request)
    try:
        result = save_resume(name, payload.get('content') or '', bool(payload.get('select', True)))
        RUNTIME_MONITOR.publish('resume_updated', {'name': name})
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get('/api/admin/prompts', summary='读取提示词')
def admin_get_prompts(request: Request):
    require_local_admin(request)
    return get_prompts()


@router.put('/api/admin/prompts', summary='保存提示词')
def admin_save_prompts(request: Request, payload: dict = Body(...)):
    require_local_admin(request)
    try:
        result = save_prompts(payload.get('values') or {})
        RUNTIME_MONITOR.publish('prompts_updated', {'keys': list((payload.get('values') or {}).keys())})
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
