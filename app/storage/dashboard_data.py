"""将投递动作日志与 SQLite 最终状态整理为仪表盘可直接消费的数据。

本模块只读取 JSONL 日志和投递数据库，不修改源数据。解析时对损坏日志、数据库暂时
不可用和不规范的城市/薪资文本采用容错降级，避免统计接口因单条历史数据失败。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
import re

from app.storage.delivery_store import (
    DELIVERY_SOURCE_ACTIONS,
    FINAL_ACTION_STATUSES,
    DeliveryStore,
    delivery_key,
)
from app.storage.io import read_jsonl


CITY_PREFIXES = sorted({
    '北京', '上海', '天津', '重庆', '深圳', '广州', '杭州', '南京', '苏州', '成都', '武汉', '西安', '长沙',
    '郑州', '青岛', '厦门', '福州', '济南', '合肥', '宁波', '东莞', '佛山', '无锡', '珠海', '惠州',
    '中山', '南昌', '昆明', '贵阳', '南宁', '海口', '三亚', '沈阳', '大连', '长春', '哈尔滨',
    '石家庄', '太原', '呼和浩特', '兰州', '西宁', '银川', '乌鲁木齐', '拉萨', '香港', '澳门',
}, key=len, reverse=True)


def extract_city(value: str | None) -> str:
    """从地点文本提取城市前缀并去掉“市”后缀；无法识别时返回空串。"""
    text = re.sub(r'\s+', '', str(value or '')).strip()
    if not text:
        return ''
    municipality = next((city for city in CITY_PREFIXES if text.startswith(city)), '')
    if municipality:
        return municipality
    match = re.match(r'^([\u4e00-\u9fff]{2,8}?)市', text)
    if match:
        return match.group(1)
    return ''


def parse_salary_details(salary: str | None) -> dict:
    """解析月薪上下限、统计基准值和薪数，返回仪表盘使用的四个标准字段。

    支持 K 制区间、元/月区间和单值 K；无法识别月薪时三个薪资值为 ``None``，
    未注明薪数时按 12 薪展示。该函数无文件或数据库副作用。
    """
    text = str(salary or '').upper().replace('Ｋ', 'K').replace(',', '')
    values: list[float] = []
    range_match = re.search(r'(\d+(?:\.\d+)?)\s*K?\s*[-~—至]\s*(\d+(?:\.\d+)?)\s*K', text)
    if range_match:
        values = [float(range_match.group(1)), float(range_match.group(2))]
    else:
        yuan_range = re.search(r'(\d{4,6})\s*[-~—至]\s*(\d{4,6})\s*元?\s*/?月', text)
        if yuan_range:
            values = [float(yuan_range.group(1)) / 1000, float(yuan_range.group(2)) / 1000]
        else:
            single = re.search(r'(\d+(?:\.\d+)?)\s*K', text)
            if single:
                values = [float(single.group(1)), float(single.group(1))]
    months_match = re.search(r'[·x×*]\s*(\d{1,2})\s*薪', text, re.IGNORECASE)
    minimum = round(min(values), 1) if values else None
    maximum = round(max(values), 1) if values else None
    return {
        'salaryMinK': minimum,
        'salaryMaxK': maximum,
        'salaryK': minimum,
        'salaryMonths': int(months_match.group(1)) if months_match else 12,
    }


def _local_date_key(value: object) -> str:
    """将 ISO 时间转换为服务所在时区的日期；无效值返回空串。"""
    text = str(value or '').strip()
    if not text:
        return ''
    if text.endswith(('Z', 'z')):
        text = f'{text[:-1]}+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return ''
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.date().isoformat()


def _action_counts_by_date(actions: list[dict], action_name: str) -> tuple[list[dict], int]:
    """按本地日期聚合指定动作，并单独统计缺少有效时间的历史记录。"""
    counts: Counter[str] = Counter()
    undated = 0
    for record in actions:
        if record.get('action') != action_name:
            continue
        date_key = _local_date_key(record.get('loggedAt'))
        if date_key:
            counts[date_key] += 1
        else:
            undated += 1
    return (
        [{'date': date_key, 'count': counts[date_key]} for date_key in sorted(counts)],
        undated,
    )


def _record_status(
    record: dict,
    final_by_token: dict[str, str],
    final_by_job: dict[str, str],
    db_by_token: dict[str, dict],
    db_by_job: dict[tuple[str, str], dict],
) -> str:
    """按动作终态、令牌数据库记录、岗位记录的优先级推导展示状态。"""
    action = record.get('action')
    if action in FINAL_ACTION_STATUSES:
        return FINAL_ACTION_STATUSES[action]
    token = record.get('claimToken') or ''
    if token in final_by_token:
        return final_by_token[token]
    job_key = delivery_key(record.get('company') or '', record.get('title') or '')
    if job_key in final_by_job:
        return final_by_job[job_key]
    if token in db_by_token:
        return db_by_token[token].get('status') or 'queued'
    key = ((record.get('company') or '').strip(), (record.get('title') or '').strip())
    if key in db_by_job:
        return db_by_job[key].get('status') or 'queued'
    return 'reserved' if action == 'delivery_claimed' else 'queued'


def delivery_sources(actions: list[dict]) -> list[dict]:
    """筛选每条可见投递的源动作，并按 ``claimToken`` 去重且保留原顺序。

    返回项包含稳定展示 ID、原列表下标和原记录引用；函数不会修改传入动作列表。
    """
    sources = []
    seen_tokens: set[str] = set()
    seen_jobs: set[str] = set()
    for index, record in enumerate(actions):
        if record.get('action') not in DELIVERY_SOURCE_ACTIONS:
            continue
        token = record.get('claimToken') or ''
        if token and token in seen_tokens:
            continue
        job_key = delivery_key(record.get('company') or '', record.get('title') or '')
        if job_key and job_key in seen_jobs:
            continue
        if token:
            seen_tokens.add(token)
        if job_key:
            seen_jobs.add(job_key)
        logged_at = record.get('loggedAt') or ''
        timestamp_id = index
        try:
            timestamp_id = int(datetime.fromisoformat(logged_at).timestamp())
        except (TypeError, ValueError):
            pass
        sources.append({
            'id': str(record.get('eventId') or f'{timestamp_id}-{index}'),
            'index': index,
            'record': record,
        })
    return sources


def load_dashboard_data(action_log_path: Path, delivery_store: DeliveryStore) -> dict:
    """聚合动作 JSONL 与投递数据库，返回统计摘要和倒序投递明细。

    日志路径和存储对象均按只读方式使用。数据库状态用于弥补异步日志中的中间态；源文件缺失或数据库
    暂不可读时仍返回结构完整的空数据或日志侧结果。
    """
    actions = read_jsonl(action_log_path)
    db_by_token, db_by_job = delivery_store.status_indexes()

    # 同一令牌可能有多条动作；按日志顺序保留最后出现的终态，再与数据库状态交叉校正。
    final_by_token: dict[str, str] = {}
    final_by_job: dict[str, str] = {}
    for record in actions:
        token = record.get('claimToken') or ''
        status = FINAL_ACTION_STATUSES.get(record.get('action'))
        if token and status:
            final_by_token[token] = status
        job_key = delivery_key(record.get('company') or '', record.get('title') or '')
        if job_key and status:
            final_by_job[job_key] = status

    deliveries = []
    for source in delivery_sources(actions):
        index = source['index']
        record = source['record']
        token = record.get('claimToken') or ''
        logged_at = record.get('loggedAt') or ''
        try:
            parsed_at = datetime.fromisoformat(logged_at)
            timestamp = parsed_at.isoformat(timespec='seconds')
        except (TypeError, ValueError):
            timestamp = logged_at

        salary = (record.get('salary') or '').strip()
        salary_details = parse_salary_details(salary)
        status = _record_status(record, final_by_token, final_by_job, db_by_token, db_by_job)
        deliveries.append({
            'id': source['id'],
            'loggedAt': timestamp,
            'company': (record.get('company') or '未记录公司').strip(),
            'title': (record.get('title') or '未记录岗位').strip(),
            'salary': salary or '面议',
            **salary_details,
            'location': (record.get('location') or record.get('city') or '').strip(),
            'city': extract_city(record.get('city') or record.get('location')),
            'industry': (record.get('industry') or '').strip(),
            'experience': (record.get('experience') or '').strip(),
            'education': (record.get('education') or '').strip(),
            'keyword': (record.get('keyword') or '').strip(),
            'hrActive': (record.get('hrActive') or '').strip(),
            'hrActiveLevel': (record.get('hrActiveLevel') or 'unknown').strip(),
            'greetingMode': (record.get('greetingMode') or '').strip(),
            'status': status,
            'score': record.get('score'),
            'accountId': (record.get('accountId') or '默认账号').strip(),
            'scene': record.get('scene') or 'search',
            'claimToken': token,
            'sourceAction': record.get('action') or '',
            'canDelete': status not in {'reserved', 'queued'},
        })

    deliveries.sort(key=lambda item: item.get('loggedAt') or '', reverse=True)
    action_counts = Counter(record.get('action') for record in actions)
    evaluated_by_date, undated_evaluated = _action_counts_by_date(actions, 'job_decision_consumed')
    valid_salary = [item['salaryK'] for item in deliveries if item['salaryK'] is not None]
    unique_companies = {item['company'] for item in deliveries if item['company'] != '未记录公司'}
    active_dates = {item['loggedAt'][:10] for item in deliveries if item.get('loggedAt')}

    return {
        'generatedAt': datetime.now().isoformat(timespec='seconds'),
        'summary': {
            'totalApplications': len(deliveries),
            'uniqueCompanies': len(unique_companies),
            'averageSalaryK': round(sum(valid_salary) / len(valid_salary), 1) if valid_salary else None,
            'activeDays': len(active_dates),
            'evaluatedJobs': action_counts.get('job_decision_consumed', 0),
            'evaluatedJobsByDate': evaluated_by_date,
            'undatedEvaluatedJobs': undated_evaluated,
            'belowThreshold': action_counts.get('job_below_threshold', 0),
            'queueFailures': action_counts.get('greet_queue_failed', 0),
            'resumesSent': action_counts.get('resume_sent', 0),
        },
        'deliveries': deliveries,
    }
