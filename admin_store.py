from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re

from config import Config
import prompts


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / 'user_config.json'
RESUME_SUFFIXES = {'.md', '.txt'}
MAX_RESUME_SIZE = 2 * 1024 * 1024


def _read_json(path: Path, default):
    if not path.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return copy.deepcopy(default)


def _atomic_write_text(path: Path, content: str) -> None:
    temp_path = path.with_name(f'.{path.name}.tmp')
    with temp_path.open('w', encoding='utf-8', newline='\n') as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, path)


def _validate_number(container: dict, key: str, minimum: float, maximum: float) -> None:
    value = container.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f'{key} 必须在 {minimum} 到 {maximum} 之间')


def validate_config(config: dict) -> None:
    if not isinstance(config, dict):
        raise ValueError('配置必须是 JSON 对象')
    for key in ('frontend', 'backend', 'scoring'):
        if not isinstance(config.get(key), dict):
            raise ValueError(f'{key} 必须是对象')
    tags = config.get('tags')
    if not isinstance(tags, list) or not tags or len(tags) > 80:
        raise ValueError('tags 必须是 1 到 80 个关键词的数组')
    if any(not isinstance(tag, str) or not tag.strip() or len(tag) > 80 for tag in tags):
        raise ValueError('tags 中存在无效关键词')
    for key in ('introduce', 'character', 'resume_content'):
        if not isinstance(config.get(key), str):
            raise ValueError(f'{key} 必须是字符串')

    frontend = config['frontend']
    if not isinstance(frontend.get('serverHost'), str) or not frontend['serverHost'].strip():
        raise ValueError('serverHost 必须是非空地址')
    if not isinstance(frontend.get('onlyGreet'), bool):
        raise ValueError('onlyGreet 必须是开关值')
    _validate_number(frontend, 'thread', 0, 100)
    _validate_number(frontend, 'resumeIndex', 0, 50)
    for key in ('timestampTimeout', 'manualFilterWaitMs', 'roundRestartDelayMs', 'detailTimeout', 'greetTimeout', 'preloadScrollWaitMs'):
        _validate_number(frontend, key, 0, 600000)
    for key in ('maxEmptyRounds', 'preloadStableRoundsLimit', 'preloadMaxRounds', 'preloadActivateCardEvery'):
        _validate_number(frontend, key, 0, 10000)
    _validate_number(frontend, 'preloadScrollPixels', 0, 5000)

    backend = config['backend']
    _validate_number(backend, 'job_score_delay_base_ms', 0, 600000)
    _validate_number(backend, 'job_score_delay_jitter_ms', 0, 600000)
    _validate_number(backend, 'daily_greet_limit', 1, 10000)
    if Path(str(backend.get('delivery_db_path') or '')).name != backend.get('delivery_db_path'):
        raise ValueError('delivery_db_path 只能是文件名')

    expected_scoring_groups = {'title_deduction_keywords', 'detail_deduction_keywords'}
    if set(config['scoring']) != expected_scoring_groups:
        raise ValueError('岗位扣星规则必须包含职位名称和职位描述两个规则组')
    for group_name, values in config['scoring'].items():
        if not isinstance(values, dict) or len(values) > 1000:
            raise ValueError(f'scoring.{group_name} 必须是关键词分数字典')
        for keyword, score in values.items():
            if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 100:
                raise ValueError(f'scoring.{group_name} 包含无效关键词')
            if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 5:
                raise ValueError(f'scoring.{group_name}.{keyword} 扣星数必须是 1 到 5 的整数')


def get_public_config() -> dict:
    effective = Config.reload()
    public = copy.deepcopy(effective)
    return {
        'config': public,
        'revision': CONFIG_PATH.stat().st_mtime_ns if CONFIG_PATH.exists() else 0,
        'restartRequiredFields': ['backend.delivery_db_path'],
    }


def save_config(payload: dict) -> dict:
    incoming = payload.get('config') if isinstance(payload, dict) else None
    if not isinstance(incoming, dict):
        raise ValueError('缺少 config 对象')
    # The admin page submits the complete visual form. Treat it as a replacement
    # instead of recursively merging keyword maps; otherwise deleted scoring
    # keywords are silently restored from the previous JSON/default config.
    merged = copy.deepcopy(incoming)
    # 大模型接口已迁移到 .env，绝不写回 user_config.json。
    merged.pop('llm', None)
    for legacy_key in ('resume_name', 'think_model', 'chat_model'):
        merged.pop(legacy_key, None)
    validate_config(merged)
    _atomic_write_text(CONFIG_PATH, json.dumps(merged, ensure_ascii=False, indent=2) + '\n')
    Config.reload()
    return get_public_config()


def _safe_resume_path(name: str) -> Path:
    clean_name = Path(name or '').name
    if clean_name != name or not clean_name or not re.fullmatch(r'[\w\-.\u4e00-\u9fff]+', clean_name):
        raise ValueError('简历文件名无效')
    path = (ROOT / clean_name).resolve()
    if path.parent != ROOT.resolve() or path.suffix.lower() not in RESUME_SUFFIXES:
        raise ValueError('只允许项目根目录下的 .md 或 .txt 简历')
    return path


def _auto_selected_resume(items: list[dict]) -> str:
    """无简历指针后，按优先级自动选择：简历_精简.md → resume.md → 首个存在的文件。"""
    for preferred in ('简历_精简.md', 'resume.md'):
        if any(item['name'] == preferred and item['exists'] for item in items):
            return preferred
    return next((item['name'] for item in items if item['exists']), '')


def list_resumes() -> dict:
    names = set()
    for path in ROOT.iterdir():
        if not path.is_file() or path.suffix.lower() not in RESUME_SUFFIXES:
            continue
        lower = path.name.lower()
        if 'resume' in lower or '简历' in path.name:
            names.add(path.name)
    items = []
    for name in sorted(names):
        path = _safe_resume_path(name)
        items.append({
            'name': name,
            'exists': path.exists(),
            'size': path.stat().st_size if path.exists() else 0,
            'updatedAt': path.stat().st_mtime if path.exists() else None,
        })
    selected = _auto_selected_resume(items)
    return {'selected': selected, 'configured': selected, 'items': items}


def read_resume(name: str) -> dict:
    path = _safe_resume_path(name)
    content = path.read_text(encoding='utf-8') if path.exists() else ''
    return {'name': name, 'content': content, 'size': len(content.encode('utf-8'))}


def save_resume(name: str, content: str, select: bool = True) -> dict:
    path = _safe_resume_path(name)
    if not isinstance(content, str):
        raise ValueError('简历内容必须是文本')
    if len(content.encode('utf-8')) > MAX_RESUME_SIZE:
        raise ValueError('简历文件不能超过 2MB')
    _atomic_write_text(path, content)
    # 简历不再有“当前选中”指针，保存即写入文件；自动选择逻辑见 _auto_selected_resume。
    return read_resume(name)


def get_prompts() -> dict:
    return {
        'items': [
            {'key': key, 'label': prompts.PROMPT_LABELS[key], 'content': value}
            for key, value in prompts.get_prompt_values().items()
        ]
    }


def save_prompts(values: dict) -> dict:
    prompts.save_prompt_values(values)
    return get_prompts()
