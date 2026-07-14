"""求职自动化服务的 FastAPI 入口。

本模块统一暴露岗位评分、投递占位、招呼语生成、运行监控和本地管理接口，
并在进程启动时初始化投递数据库及迁移旧版日志记录。
"""

from datetime import datetime
import asyncio
import random
import json
import os
from pathlib import Path
import threading
import sqlite3
import uuid
from fastapi import FastAPI, Body, HTTPException
from starlette.requests import Request
from config import Config
from delivery_store import DeliveryStore, delivery_key
from llm_manager import LLM_MANAGER
from llm_env_store import public_llm_config, save_llm_config
import llm_env_store
from dashboard_data import load_dashboard_data, delivery_sources
from admin_store import get_public_config, save_config, list_resumes, read_resume, save_resume, select_resume, get_prompts, save_prompts
from runtime_monitor import RUNTIME_MONITOR


app = FastAPI()

# 业务日志仍使用 JSONL 便于人工检查；投递去重、状态和配额以 SQLite 为准。
LOG_PATH = Path(__file__).resolve().parent / 'job_decisions.jsonl'
ACTION_LOG_PATH = Path(__file__).resolve().parent / 'job_actions.jsonl'
GREETED_PATH = Path(__file__).resolve().parent / 'greeted_jobs.jsonl'
AI_FILTER_PATH = Path(__file__).resolve().parent / 'ai_filter_log.jsonl'
DELIVERY_DB_PATH = Path(__file__).resolve().parent / Config.backend.get('delivery_db_path', 'delivery_state.db')
DELIVERY_STORE = DeliveryStore(
    DELIVERY_DB_PATH,
    daily_limit=Config.backend.get('daily_greet_limit', 90),
)
DASHBOARD_DIR = Path(__file__).resolve().parent / 'dashboard'
legacy_imported = DELIVERY_STORE.import_legacy_jsonl(GREETED_PATH)
action_imported = DELIVERY_STORE.import_action_log(ACTION_LOG_PATH)
print(
    f"[启动] 投递协调数据库: {DELIVERY_DB_PATH}，"
    f"迁移旧记录 {legacy_imported + action_imported} 条",
    flush=True,
)

# 每类文件和内存任务表使用独立锁，避免并发请求写入交叉或读到中间状态。
_DECISION_LOG_LOCK = threading.Lock()
_ACTION_LOG_LOCK = threading.Lock()
_AI_FILTER_LOG_LOCK = threading.Lock()
_GREETED_LOG_LOCK = threading.Lock()
_INTRODUCE_JOB_LOCK = threading.Lock()
_INTRODUCE_JOBS: dict[str, dict] = {}
_INTRODUCE_JOB_BY_CLAIM: dict[str, str] = {}
_INTRODUCE_JOB_TTL_SECONDS = 15 * 60


def _append_jsonl(path: Path, record: dict, lock: threading.Lock):
    """在指定锁保护下追加一条 UTF-8 JSONL 记录。"""
    line = json.dumps(record, ensure_ascii=False) + '\n'
    with lock:
        with path.open('a', encoding='utf-8') as file:
            file.write(line)
            file.flush()


def _read_jsonl_records(path: Path) -> list[dict]:
    """读取有效的 JSONL 对象，并跳过无法解析的历史坏行。"""
    if not path.exists():
        return []
    records = []
    with path.open('r', encoding='utf-8') as file:
        for line in file:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def _replace_jsonl(path: Path, records: list[dict]) -> None:
    """通过同目录临时文件原子替换整份 JSONL，避免留下半写文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('w', encoding='utf-8', newline='') as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False) + '\n')
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def append_job_decision_log(result: dict, raw_job: str, delay_ms: int):
    """记录一次岗位评分决策及其扣分明细。"""
    log_record = {
        'loggedAt': datetime.now().isoformat(timespec='seconds'),
        'title': result.get('title'),
        'detail': result.get('detail'),
        'matchedField': result.get('matched_field'),
        'keyword': result.get('keyword'),
        'score': result.get('score'),
        'introduce': result.get('introduce'),
        'resumeIndex': result.get('resumeIndex'),
        'titleScore': result.get('title_score'),
        'detailScore': result.get('detail_score'),
        'comboScore': result.get('combo_score'),
        'titlePenaltyScore': result.get('title_penalty_score'),
        'penaltyScore': result.get('penalty_score'),
        'deductions': result.get('deductions') or [],
        'reason': result.get('reason'),
        'delayMs': delay_ms,
        'rawJob': raw_job,
    }
    _append_jsonl(LOG_PATH, log_record, _DECISION_LOG_LOCK)


def append_job_action_log(action: dict):
    """持久化浏览器动作，并同步更新运行监控状态。"""
    action_record = {
        **action,
        'eventId': str(action.get('eventId') or uuid.uuid4()),
        'loggedAt': datetime.now().isoformat(timespec='seconds'),
    }
    _append_jsonl(ACTION_LOG_PATH, action_record, _ACTION_LOG_LOCK)
    RUNTIME_MONITOR.record_action(action_record)


def _require_local_admin(request: Request) -> None:
    """拒绝非回环地址访问管理接口，限制配置和简历的暴露范围。"""
    host = request.client.host if request.client else ''
    if host not in {'127.0.0.1', '::1', 'localhost', 'testclient'}:
        raise HTTPException(status_code=403, detail='管理功能只允许从本机访问')


def append_ai_filter_log(title: str, salary: str, detail: str, keyword_score: int, ai_passed: bool, ai_reason: str):
    """记录 AI 岗位筛选结果，职位描述最多保留 500 字符。"""
    record = {
        'loggedAt': datetime.now().isoformat(timespec='seconds'),
        'title': title,
        'salary': salary,
        'detail': detail[:500] if detail else '',
        'keywordScore': keyword_score,
        'aiPassed': ai_passed,
        'aiReason': ai_reason,
    }
    _append_jsonl(AI_FILTER_PATH, record, _AI_FILTER_LOG_LOCK)


@app.get("/tags", summary="获取职位标签")
async def get_tags():
    """返回浏览器脚本依次搜索的职位关键词。"""
    return {
        'tags': Config.tags
    }


@app.get("/get-introduce", summary="获取自我介绍")
async def get_introduce():
    """返回 LLM 降级时使用的固定招呼语。"""
    return {
        'introduce': Config.get_default_introduce()
    }


@app.get("/client-config", summary="获取前端运行配置")
async def get_client_config():
    """返回允许浏览器脚本读取的运行配置。"""
    return Config.get_client_config()


@app.get('/dashboard', include_in_schema=False)
async def dashboard_page():
    """提供管理面板的 HTML 入口文件。"""
    from fastapi.responses import FileResponse
    return FileResponse(DASHBOARD_DIR / 'index.html')


@app.get('/dashboard/styles.css', include_in_schema=False)
async def dashboard_styles():
    """提供管理面板的样式表。"""
    from fastapi.responses import FileResponse
    return FileResponse(DASHBOARD_DIR / 'styles.css', media_type='text/css')


@app.get('/dashboard/app.js', include_in_schema=False)
async def dashboard_script():
    """提供管理面板的前端脚本。"""
    from fastapi.responses import FileResponse
    return FileResponse(DASHBOARD_DIR / 'app.js', media_type='application/javascript')


@app.get('/dashboard/china.json', include_in_schema=False)
async def dashboard_china_map():
    """提供省级投递热力地图的地理边界数据。"""
    from fastapi.responses import FileResponse
    return FileResponse(DASHBOARD_DIR / 'china.json', media_type='application/json')


@app.get('/dashboard/china-cities.json', include_in_schema=False)
async def dashboard_china_city_map():
    """提供地级市投递热力地图的地理边界数据。"""
    from fastapi.responses import FileResponse
    return FileResponse(DASHBOARD_DIR / 'china-cities.json', media_type='application/json')


@app.get('/api/dashboard', summary='获取投递统计面板数据')
def get_dashboard_data():
    """汇总动作日志和投递数据库，生成管理面板统计数据。"""
    with _ACTION_LOG_LOCK:
        return load_dashboard_data(ACTION_LOG_PATH, DELIVERY_DB_PATH)


@app.post('/api/admin/deliveries/delete', summary='删除单条或批量投递记录')
def admin_delete_deliveries(request: Request, payload: dict = Body(...)):
    """按面板记录 ID 删除同一投递在日志和数据库中的关联数据。"""
    _require_local_admin(request)
    record_ids = {str(value).strip() for value in (payload.get('ids') or []) if str(value).strip()}
    if not record_ids:
        raise HTTPException(status_code=400, detail='请选择需要删除的投递记录')
    if len(record_ids) > 500:
        raise HTTPException(status_code=400, detail='单次最多删除 500 条记录')

    with _ACTION_LOG_LOCK:
        actions = _read_jsonl_records(ACTION_LOG_PATH)
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

        # 一次可见投递可能对应多条动作日志，需按令牌和公司岗位同时清理关联行。
        job_keys = {delivery_key(company, title) for company, title in jobs}
        for index, record in enumerate(actions):
            if (record.get('claimToken') or '').strip() in claim_tokens:
                selected_indices.add(index)
                continue
            key = delivery_key(record.get('company') or '', record.get('title') or '')
            if key and key in job_keys:
                selected_indices.add(index)

        try:
            database_result = DELIVERY_STORE.delete_history(claim_tokens, jobs)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

        remaining_actions = [record for index, record in enumerate(actions) if index not in selected_indices]
        _replace_jsonl(ACTION_LOG_PATH, remaining_actions)

    if jobs and GREETED_PATH.exists():
        with _GREETED_LOG_LOCK:
            greeted = _read_jsonl_records(GREETED_PATH)
            remaining_greeted = [
                record for record in greeted
                if delivery_key(record.get('company') or '', record.get('title') or '') not in job_keys
            ]
            _replace_jsonl(GREETED_PATH, remaining_greeted)

    deleted_visible = len(selected)
    RUNTIME_MONITOR.audit('delivery_records_deleted', {
        'visibleRecords': deleted_visible,
        'actionRows': len(selected_indices),
        'databaseRows': database_result.get('deleted', 0),
    })
    return {
        'success': True,
        'deleted': deleted_visible,
        'deletedActionRows': len(selected_indices),
        'deletedDatabaseRows': database_result.get('deleted', 0),
    }


@app.post('/api/runtime/heartbeat', summary='脚本运行心跳与日志批量上报')
def runtime_heartbeat(payload: dict = Body(...)):
    """接收浏览器脚本心跳和批量运行事件。"""
    try:
        return RUNTIME_MONITOR.heartbeat(payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get('/api/runtime', summary='获取脚本接入与运行状态')
def runtime_snapshot():
    """返回运行监控快照、近期事件和 LLM 健康状态。"""
    return {
        **RUNTIME_MONITOR.snapshot(),
        'events': RUNTIME_MONITOR.recent_events(120),
        'llm': LLM_MANAGER.snapshot(),
    }


def _control_quota(account_id: str) -> dict:
    """合并数据库用量与控制中心策略，计算账号的实时剩余配额。"""
    quota = DELIVERY_STORE.quota_status(account_id)
    policy = RUNTIME_MONITOR.effective_control('', account_id).get('account') or {}
    configured_limit = int(policy.get('dailyLimit') or quota['limit'])
    quota['limit'] = configured_limit
    quota['remaining'] = max(0, configured_limit - quota['count'])
    quota['reached'] = quota['count'] >= configured_limit
    return quota


@app.get('/api/control/state', summary='获取控制中心完整状态')
def control_state(request: Request):
    """返回客户端、账号策略和实时投递配额。"""
    _require_local_admin(request)
    state = RUNTIME_MONITOR.control_state()
    account_ids = {
        client.get('accountId') for client in state['clients'] if client.get('accountId')
    } | set(state['accounts'])
    state['quotas'] = {account_id: _control_quota(account_id) for account_id in account_ids}
    return state


@app.put('/api/control/accounts/{account_id}', summary='更新账号别名、配额与运行策略')
def control_update_account(account_id: str, request: Request, payload: dict = Body(...)):
    """更新指定账号允许由网页管理的控制策略字段。"""
    _require_local_admin(request)
    try:
        safe_payload = {key: value for key, value in payload.items() if key in {'alias', 'dailyLimit', 'notes'}}
        policy = RUNTIME_MONITOR.update_account(account_id, safe_payload)
        return {'policy': policy, 'quota': _control_quota(account_id)}
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.put('/api/control/errors/{error_id}', summary='处理或重新打开异常')
def control_resolve_error(error_id: str, request: Request, payload: dict = Body(default={})):
    """切换一条运行异常的已处理状态。"""
    _require_local_admin(request)
    try:
        return RUNTIME_MONITOR.resolve_error(error_id, bool(payload.get('resolved', True)))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get('/api/control/health', summary='检查控制中心、数据库与配置健康状态')
def control_health(request: Request):
    """检查运行监控、配置、数据库和 LLM 接口的综合健康状态。"""
    _require_local_admin(request)
    llm_state = LLM_MANAGER.snapshot()
    checks = {
        'runtime': {'ok': True},
        'config': {'ok': True},
        'database': {'ok': False},
        'llm': {
            # 没有启用任何接口视为“无需检查”，有接口时只要还有健康 provider 即通过。
            'ok': not llm_state['enabled'] or any(p.get('circuit') != 'open' for p in llm_state['providers']),
            **llm_state,
        },
    }
    try:
        with sqlite3.connect(DELIVERY_DB_PATH, timeout=2) as connection:
            connection.execute('SELECT 1').fetchone()
        checks['database'] = {'ok': True, 'path': str(DELIVERY_DB_PATH), 'sizeBytes': DELIVERY_DB_PATH.stat().st_size}
    except Exception as error:
        checks['database'] = {'ok': False, 'error': str(error)}
    return {
        'ok': all(check['ok'] for check in checks.values()),
        'checkedAt': datetime.now().isoformat(timespec='seconds'),
        'checks': checks,
        **RUNTIME_MONITOR.diagnostics(),
    }


@app.get('/api/control/diagnostics', summary='下载式控制中心诊断信息')
def control_diagnostics(request: Request):
    """汇总运行状态、文件信息和配置摘要供故障诊断。"""
    _require_local_admin(request)
    return {
        'generatedAt': datetime.now().isoformat(timespec='seconds'),
        'runtime': RUNTIME_MONITOR.diagnostics(),
        'state': RUNTIME_MONITOR.control_state(300),
        'files': {
            path.name: {'exists': path.exists(), 'sizeBytes': path.stat().st_size if path.exists() else 0}
            for path in (LOG_PATH, ACTION_LOG_PATH, AI_FILTER_PATH, DELIVERY_DB_PATH)
        },
        'config': {
            'tags': len(Config.tags),
            'llmEnabled': LLM_MANAGER.available(),
            'deliveryDatabase': str(DELIVERY_DB_PATH),
        },
        'llm': LLM_MANAGER.snapshot(),
    }


@app.post('/api/control/reload-config', summary='重新加载运行配置')
def control_reload_config(request: Request):
    """热加载用户配置和 LLM 接口，并重置相关运行缓存。"""
    _require_local_admin(request)
    Config.reload()
    LLM_MANAGER.reload()
    LLM_MANAGER.reset_circuits(clear_cache=True)
    DELIVERY_STORE.daily_limit = max(1, int(Config.backend.get('daily_greet_limit', DELIVERY_STORE.daily_limit)))
    RUNTIME_MONITOR.audit('config_reloaded', {})
    RUNTIME_MONITOR.publish('config_updated', {'restartRequired': []})
    return {'success': True, 'reloadedAt': datetime.now().isoformat(timespec='seconds')}


@app.get('/api/runtime/events', include_in_schema=False)
async def runtime_events(request: Request, cursor: int = 0):
    """通过 SSE 推送游标之后的运行事件，并定期发送保活注释。"""
    from fastapi.responses import StreamingResponse

    async def stream():
        current = max(0, cursor)
        keep_alive_at = asyncio.get_running_loop().time()
        try:
            while not await request.is_disconnected():
                # 不在线程中等待 Condition：协程取消无法终止线程等待，会拖慢或阻塞服务关闭。
                events = RUNTIME_MONITOR.events_after(current, 0)
                if events:
                    for event in events:
                        current = event['id']
                        yield f"id: {event['id']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
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


@app.get('/api/admin/config', summary='读取网页管理配置')
def admin_get_config(request: Request):
    """返回经过脱敏和整理的网页可管理配置。"""
    _require_local_admin(request)
    return get_public_config()


@app.put('/api/admin/config', summary='保存网页管理配置')
def admin_save_config(request: Request, payload: dict = Body(...)):
    """校验并保存网页参数配置，同时通知在线客户端。"""
    _require_local_admin(request)
    try:
        result = save_config(payload)
        LLM_MANAGER.reset_circuits(clear_cache=True)
        DELIVERY_STORE.daily_limit = max(1, int(Config.backend.get('daily_greet_limit', DELIVERY_STORE.daily_limit)))
        RUNTIME_MONITOR.publish('config_updated', {'restartRequired': result['restartRequiredFields']})
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get('/api/admin/llm', summary='读取大模型接口配置')
def admin_get_llm(request: Request):
    """返回隐藏密钥后的大模型接口配置。"""
    _require_local_admin(request)
    return public_llm_config()


@app.put('/api/admin/llm', summary='保存大模型接口配置到 .env')
def admin_save_llm(request: Request, payload: dict = Body(...)):
    """保存大模型接口配置并重新加载接口管理器。"""
    _require_local_admin(request)
    try:
        result = save_llm_config(payload)
        LLM_MANAGER.reload()
        RUNTIME_MONITOR.publish('llm_updated', {'providerCount': len(result['providers'])})
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post('/api/admin/llm/test', summary='测试大模型接口连通性')
async def admin_test_llm(request: Request, payload: dict = Body(default={})):
    """测试临时配置、指定接口或全部已保存接口的连通性。"""
    _require_local_admin(request)
    provider = payload.get('provider') if isinstance(payload, dict) else None
    if isinstance(provider, dict):
        return await LLM_MANAGER.test_provider_payload(provider)
    index = payload.get('index') if isinstance(payload, dict) else None
    if isinstance(index, int):
        return await LLM_MANAGER.test_provider(index)
    return {'results': await LLM_MANAGER.test_all()}


@app.get('/api/admin/resumes', summary='列出可管理简历')
def admin_list_resumes(request: Request):
    """列出简历目录中的可管理文件及当前选中项。"""
    _require_local_admin(request)
    return list_resumes()


@app.put('/api/admin/resumes/current', summary='设置 LLM 当前使用的简历')
def admin_select_resume(request: Request, payload: dict = Body(...)):
    """持久化选择 LLM 后续任务使用的当前简历。"""
    _require_local_admin(request)
    try:
        result = select_resume(str(payload.get('name') or ''))
        RUNTIME_MONITOR.publish('resume_selected', {'name': result['selected']})
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get('/api/admin/resumes/{name}', summary='读取简历')
def admin_read_resume(name: str, request: Request):
    """读取指定名称的受管简历。"""
    _require_local_admin(request)
    try:
        return read_resume(name)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.put('/api/admin/resumes/{name}', summary='保存简历')
def admin_save_resume(name: str, request: Request, payload: dict = Body(...)):
    """保存受管简历，并可同时将其设为当前简历。"""
    _require_local_admin(request)
    try:
        result = save_resume(name, payload.get('content') or '', bool(payload.get('select', True)))
        RUNTIME_MONITOR.publish('resume_updated', {'name': name})
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get('/api/admin/prompts', summary='读取提示词')
def admin_get_prompts(request: Request):
    """返回全部提示词的当前值和管理元数据。"""
    _require_local_admin(request)
    return get_prompts()


@app.put('/api/admin/prompts', summary='保存提示词')
def admin_save_prompts(request: Request, payload: dict = Body(...)):
    """校验并保存提示词覆盖，然后通知在线客户端。"""
    _require_local_admin(request)
    try:
        result = save_prompts(payload.get('values') or {})
        RUNTIME_MONITOR.publish('prompts_updated', {'keys': list((payload.get('values') or {}).keys())})
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/get-job-score", summary="获取职位匹配度")
async def get_job_score(job: str = Body(..., description="职位信息")):
    """执行关键词扣星和可选 AI 筛选，返回浏览器投递流程所需的决策结果。"""
    from core import evaluateJobMatch, llmJobFilter, __extract_job_fields
    # 第一步：关键词评分（同步，快速）
    result = evaluateJobMatch(job)
    title, detail = __extract_job_fields(job)
    delay_ms = max(0, Config.job_score_delay_base_ms + random.randint(
        -Config.job_score_delay_jitter_ms,
        Config.job_score_delay_jitter_ms,
    ))
    use_ai_filter = LLM_MANAGER.available() and LLM_MANAGER.job_filter_enabled
    # 第二步：只执行岗位筛选和评分延迟。招呼语必须在全部条件通过后单独生成。
    (ai_pass, ai_reason), _ = await asyncio.gather(
        llmJobFilter(title, '', detail),
        asyncio.sleep(delay_ms / 1000),
    )
    # 记录 AI 筛选结果
    if use_ai_filter:
        append_ai_filter_log(title, '', detail, result['score'], ai_pass, ai_reason)
    # 第三步：AI 筛选（开启时作为第二道关卡）
    if use_ai_filter and not ai_pass:
        result['score'] = 0
        result['reason'] = f'AI筛选不通过: {ai_reason}'
    time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    display_title = result['title'] or '未识别标题'
    keyword = result['keyword'] or '无'
    matched_field_map = {
        'title': '岗位名称',
        'detail': '职位描述',
        'none': '未命中',
        'title_negative': '标题负向拦截',
    }
    print(
        f"[{time_str}] /get-job-score | "
        f"title={display_title} | "
        f"matched={matched_field_map.get(result['matched_field'], result['matched_field'])} | "
        f"keyword={keyword} | "
        f"title_score={result['title_score']} | "
        f"detail_score={result['detail_score']} | "
        f"combo_score={result['combo_score']} | "
        f"title_penalty_score={result.get('title_penalty_score', 0)} | "
        f"penalty_score={result['penalty_score']} | "
        f"delay_ms={delay_ms} | "
        f"score={result['score']} | "
        f"reason={result['reason']}",
        flush=True
    )
    for deduction in result.get('deductions') or []:
        print(
            f"[评分扣星] title={display_title} | "
            f"位置={deduction.get('fieldLabel') or deduction.get('field')} | "
            f"关键词={deduction.get('keyword')} | "
            f"扣除={deduction.get('deductStars')}星",
            flush=True,
        )
    append_job_decision_log(result, job, delay_ms)
    return {
        **result,
        'introduce': Config.introduce,
        'introduceGenerated': False,
        'resumeIndex': Config.frontend.get('resumeIndex', 0),
    }


def _validate_introduce_payload(payload: dict) -> dict:
    """校验投递令牌、状态及公司岗位归属，阻止提前或串单生成招呼语。"""
    claim_token = (payload.get('claimToken') or '').strip()
    claim = DELIVERY_STORE.claim_status(claim_token)
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
    """在投递校验完成后调用核心模块生成招呼语。"""
    from core import generateCustomIntroduce

    print(
        f"[LLM] 所有投递条件已通过，开始生成最终招呼语 | "
        f"company={payload['company']} | title={payload['title']}",
        flush=True,
    )
    introduce_result = await generateCustomIntroduce(
        payload['title'],
        payload['salary'],
        payload['detail'],
        return_meta=True,
    )
    return introduce_result


def _prune_introduce_jobs(now: float) -> None:
    """清理已完成且超过保留时间的后台招呼语任务。"""
    expired = []
    for job_id, job in _INTRODUCE_JOBS.items():
        if job['status'] != 'pending' and now - job['updatedAt'] >= _INTRODUCE_JOB_TTL_SECONDS:
            expired.append(job_id)
    for job_id in expired:
        job = _INTRODUCE_JOBS.pop(job_id)
        if _INTRODUCE_JOB_BY_CLAIM.get(job['claimToken']) == job_id:
            _INTRODUCE_JOB_BY_CLAIM.pop(job['claimToken'], None)


def _introduce_job_response(job_id: str, job: dict) -> dict:
    """将内部任务状态转换为轮询接口的公开响应。"""
    response = {'jobId': job_id, 'status': job['status']}
    if job['status'] == 'completed':
        response.update(job['result'])
    elif job['status'] == 'failed':
        response['error'] = job['error']
    return response


async def _run_introduce_job(job_id: str, payload: dict) -> None:
    """执行后台招呼语任务，并在线程锁内提交最终状态。"""
    try:
        result = await _generate_introduce_result(payload)
        status = 'completed'
        error = ''
    except asyncio.CancelledError:
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


@app.post('/generate-introduce', summary='在投递条件全部通过后生成最终招呼语')
async def generate_introduce(payload: dict = Body(..., description='岗位信息与已领取的投递令牌')):
    """同步生成招呼语，适用于能够等待 LLM 响应的客户端。"""
    return await _generate_introduce_result(_validate_introduce_payload(payload))


@app.post('/generate-introduce/start', summary='启动后台招呼语生成任务')
async def start_generate_introduce(payload: dict = Body(..., description='岗位信息与已领取的投递令牌')):
    """启动可轮询的招呼语任务，同一投递令牌只创建一个任务。"""
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


@app.get('/generate-introduce/status/{job_id}', summary='查询后台招呼语生成结果')
async def generate_introduce_status(job_id: str):
    """返回后台招呼语任务的当前状态或最终结果。"""
    with _INTRODUCE_JOB_LOCK:
        _prune_introduce_jobs(asyncio.get_running_loop().time())
        job = _INTRODUCE_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail='招呼语生成任务不存在或已过期')
        return _introduce_job_response(job_id, job)


@app.post("/log-action", summary="记录前端动作日志")
async def log_action(action: dict = Body(..., description="动作日志")):
    """接收并持久化单条浏览器动作日志。"""
    append_job_action_log(action)
    return {'success': True}


@app.post("/delivery/claim", summary="原子领取公司与岗位投递权")
def claim_delivery(payload: dict = Body(..., description="公司、岗位、账号与浏览器实例信息")):
    """检查重复与账号配额后，原子领取公司岗位的投递权。"""
    account_id = payload.get('accountId') or ''
    worker_id = payload.get('workerId') or ''
    company = (payload.get('company') or '').strip()
    title = (payload.get('title') or '').strip()
    existing_status = DELIVERY_STORE.company_status(company, title)
    if existing_status.get('exists'):
        existing = existing_status.get('delivery') or {}
        print(
            f'[投递判断] 重复投递，已忽略（不写投递记录、不计投递次数） | company={company} | title={title} | '
            f'existing_account={existing.get("account_id", "")} | '
            f'existing_status={existing.get("status", "")}',
            flush=True,
        )
        return {'accepted': False, 'reason': 'duplicate_job', 'existing': existing}
    quota = _control_quota(account_id)
    if quota['reached']:
        print(
            f'[投递判断] 达到每日上限 | account={account_id} | company={company} | title={title} | '
            f'count={quota.get("count")}/{quota.get("limit")}',
            flush=True,
        )
        return {'accepted': False, 'reason': 'daily_limit', **quota}
    # 前置检查便于返回友好日志，最终并发去重仍由 DeliveryStore.claim 原子保证。
    result = DELIVERY_STORE.claim(
        company=company,
        title=title,
        account_id=account_id,
        worker_id=worker_id,
        job_url=payload.get('jobUrl') or '',
    )
    if result.get('accepted'):
        effective_limit = quota['limit']
        result['limit'] = effective_limit
        result['remaining'] = max(0, effective_limit - int(result.get('count') or 0))
        print(
            f'[投递判断] 允许投递 | company={company} | title={title} | '
            f'account={account_id} | count={result.get("count")}/{result.get("limit")}',
            flush=True,
        )
    elif result.get('reason') == 'duplicate_job':
        existing = result.get('existing') or {}
        print(
            f'[投递判断] 重复投递，已忽略（不写投递记录、不计投递次数） | company={company} | title={title} | '
            f'existing_account={existing.get("account_id", "")} | '
            f'existing_status={existing.get("status", "")}',
            flush=True,
        )
    else:
        print(
            f'[投递判断] 拒绝投递 | company={company} | title={title} | reason={result.get("reason")}',
            flush=True,
        )
    return result


@app.post("/delivery/mark", summary="更新投递状态")
def mark_delivery(payload: dict = Body(..., description="领取令牌与投递状态")):
    """按领取令牌推进投递状态。"""
    try:
        return DELIVERY_STORE.mark(
            payload.get('claimToken') or '',
            payload.get('status') or '',
            payload.get('error') or '',
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/delivery/release", summary="释放尚未发起沟通的投递占位")
def release_delivery(payload: dict = Body(..., description="领取令牌与释放原因")):
    """释放尚未真正发送消息的投递占位。"""
    return DELIVERY_STORE.release(
        payload.get('claimToken') or '',
        payload.get('reason') or '',
    )


@app.post("/check-greet", summary="检查公司与岗位是否已领取或投递（兼容旧客户端）")
def check_greet(payload: dict = Body(..., description="公司名和职位名")):
    """为旧客户端检查公司岗位是否已有投递记录。"""
    company = (payload.get('company') or '').strip()
    title = (payload.get('title') or '').strip()
    status = DELIVERY_STORE.company_status(company, title)
    if status.get('exists'):
        delivery = status.get('delivery') or {}
        print(
            f'[投递预检] 重复投递，LLM 前置跳过 | company={company} | title={title} | '
            f'existing_account={delivery.get("account_id", "")} | existing_status={delivery.get("status", "")}',
            flush=True,
        )
    return {
        'greeted': status['exists'],
        'company': company,
        'title': title,
        'delivery': status.get('delivery'),
    }


@app.post("/log-greet", summary="记录已打招呼的岗位（兼容旧客户端）")
def log_greet(payload: dict = Body(..., description="公司名和职位名")):
    """为旧客户端记录已发送招呼的公司岗位。"""
    return DELIVERY_STORE.record_legacy_sent(
        payload.get('company') or '',
        payload.get('title') or '',
        payload.get('accountId') or 'legacy',
    )


@app.post("/increment-daily-greet", summary="增加每日打招呼计数（兼容旧客户端）")
def increment_daily_greet(payload: dict = Body(default={})):
    """为旧客户端增加指定账号的当日投递用量。"""
    return DELIVERY_STORE.increment_usage(payload.get('accountId') or 'legacy')


@app.post("/check-daily-limit", summary="检查账号每日打招呼次数限制")
def check_daily_limit(payload: dict = Body(default={})):
    """返回指定账号的当日投递配额状态。"""
    return _control_quota(payload.get('accountId') or 'legacy')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=47999,
        reload=False,
        timeout_graceful_shutdown=3,
    )
