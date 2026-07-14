from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3


DELIVERY_ACTIONS = {'delivery_claimed', 'greet_queued', 'chat_greet_sent'}
FINAL_ACTION_STATUSES = {
    'greet_sent': 'sent',
    'chat_greet_sent': 'sent',
    'greet_failed': 'failed_unknown',
    'chat_greet_failed': 'failed_unknown',
    'greet_queue_failed': 'failed_unknown',
}

CITY_PREFIXES = sorted({
    '北京', '上海', '天津', '重庆', '深圳', '广州', '杭州', '南京', '苏州', '成都', '武汉', '西安', '长沙',
    '郑州', '青岛', '厦门', '福州', '济南', '合肥', '宁波', '东莞', '佛山', '无锡', '珠海', '惠州',
    '中山', '南昌', '昆明', '贵阳', '南宁', '海口', '三亚', '沈阳', '大连', '长春', '哈尔滨',
    '石家庄', '太原', '呼和浩特', '兰州', '西宁', '银川', '乌鲁木齐', '拉萨', '香港', '澳门',
}, key=len, reverse=True)


def extract_city(value: str | None) -> str:
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


def _read_jsonl(path: Path) -> list[dict]:
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


def parse_salary_k(salary: str | None) -> float | None:
    """将 12-20K、15K、8-12K·13薪等格式折算为月薪中位数（K）。"""
    if not salary:
        return None
    text = str(salary).upper().replace('Ｋ', 'K').replace(',', '')
    range_match = re.search(
        r'(\d+(?:\.\d+)?)\s*K?\s*[-~—至]\s*(\d+(?:\.\d+)?)\s*K',
        text,
    )
    if range_match:
        values = [float(range_match.group(1)), float(range_match.group(2))]
    else:
        values = [float(value) for value in re.findall(r'(?<!\d)(\d+(?:\.\d+)?)\s*K', text)]
    if not values:
        return None
    return round(sum(values[:2]) / min(2, len(values)), 1)


def parse_salary_details(salary: str | None) -> dict:
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
        'salaryK': round((minimum + maximum) / 2, 1) if minimum is not None else None,
        'salaryMonths': int(months_match.group(1)) if months_match else 12,
    }


def _database_statuses(db_path: Path) -> tuple[dict[str, dict], dict[tuple[str, str], dict]]:
    if not db_path.exists():
        return {}, {}
    by_token: dict[str, dict] = {}
    by_job: dict[tuple[str, str], dict] = {}
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT company, title, account_id, claim_token, status,
                       claimed_at, queued_at, completed_at
                FROM company_deliveries
                """
            ).fetchall()
    except (sqlite3.Error, OSError):
        return {}, {}
    for row in rows:
        item = dict(row)
        token = item.get('claim_token') or ''
        if token:
            by_token[token] = item
        key = ((item.get('company') or '').strip(), (item.get('title') or '').strip())
        if key[0]:
            by_job[key] = item
    return by_token, by_job


def _record_status(record: dict, final_by_token: dict[str, str], db_by_token: dict[str, dict], db_by_job: dict[tuple[str, str], dict]) -> str:
    action = record.get('action')
    if action == 'chat_greet_sent':
        return 'sent'
    if action == 'greet_queued':
        return 'queued'
    token = record.get('claimToken') or ''
    if token in final_by_token:
        return final_by_token[token]
    if token in db_by_token:
        return db_by_token[token].get('status') or 'queued'
    key = ((record.get('company') or '').strip(), (record.get('title') or '').strip())
    if key in db_by_job:
        return db_by_job[key].get('status') or 'queued'
    return 'queued'


def delivery_sources(actions: list[dict]) -> list[dict]:
    """Return the source action behind each visible delivery row."""
    sources = []
    seen_tokens: set[str] = set()
    for index, record in enumerate(actions):
        if record.get('action') not in DELIVERY_ACTIONS:
            continue
        token = record.get('claimToken') or ''
        if token and token in seen_tokens:
            continue
        if token:
            seen_tokens.add(token)
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


def load_dashboard_data(action_log_path: Path, delivery_db_path: Path) -> dict:
    actions = _read_jsonl(action_log_path)
    db_by_token, db_by_job = _database_statuses(delivery_db_path)

    final_by_token: dict[str, str] = {}
    for record in actions:
        token = record.get('claimToken') or ''
        status = FINAL_ACTION_STATUSES.get(record.get('action'))
        if token and status:
            final_by_token[token] = status

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
        status = _record_status(record, final_by_token, db_by_token, db_by_job)
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
            'belowThreshold': action_counts.get('job_below_threshold', 0),
            'queueFailures': action_counts.get('greet_queue_failed', 0),
            'resumesSent': action_counts.get('resume_sent', 0),
        },
        'deliveries': deliveries,
    }
