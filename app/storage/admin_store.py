"""管理后台的配置、简历选择和提示词持久化适配层。

该模块负责校验管理端提交的完整配置，并通过临时文件替换方式写入
``user_config.json``；简历文件与提示词的具体读写分别委托给对应存储模块。
文件写入不提供多写者锁，HTTP/调用层应避免对同一资源并发保存。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from app import config
from app.config import Config
from app.llm import prompts
from app.storage.resume_store import (
    list_resume_files,
    read_resume_file,
    require_resume_file,
    save_resume_file,
    validate_resume_name,
)
from app.storage.io import atomic_write_text


def _validate_number(container: dict, key: str, minimum: float, maximum: float) -> None:
    """校验字典中的数值字段范围；布尔值不按整数接受，失败时抛 ``ValueError``。"""
    value = container.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f'{key} 必须在 {minimum} 到 {maximum} 之间')


def validate_config(config: dict) -> None:
    """校验一份完整的用户配置，不修改传入对象或磁盘内容。

    检查基础分组、关键词、简历名、前后端参数和扣星规则；任一字段不合法时抛出
    面向管理端展示的 ``ValueError``。
    """
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
    normalized_tags = [tag.strip().casefold() for tag in tags]
    if len(set(normalized_tags)) != len(normalized_tags):
        raise ValueError('tags 中存在重复关键词')
    for key in ('introduce', 'character', 'resume_name'):
        if not isinstance(config.get(key), str):
            raise ValueError(f'{key} 必须是字符串')
    if not isinstance(config.get('llm_greeting_enabled'), bool):
        raise ValueError('llm_greeting_enabled 必须是开关值')
    if not isinstance(config.get('scoring_enabled'), bool):
        raise ValueError('scoring_enabled 必须是开关值')
    validate_resume_name(config['resume_name'])

    frontend = config['frontend']
    if not isinstance(frontend.get('serverHost'), str) or not frontend['serverHost'].strip():
        raise ValueError('serverHost 必须是非空地址')
    if not isinstance(frontend.get('onlyGreet'), bool):
        raise ValueError('onlyGreet 必须是开关值')
    _validate_number(frontend, 'thread', 0, 100)
    _validate_number(frontend, 'resumeIndex', 0, 50)
    for key in (
        'timestampTimeout', 'roundRestartDelayMs', 'detailTimeout',
        'greetTimeout', 'preloadScrollWaitMs', 'preloadActivateCardWaitMs',
    ):
        _validate_number(frontend, key, 0, 600000)
    for key in ('maxEmptyRounds', 'preloadStableRoundsLimit', 'preloadMaxRounds', 'preloadActivateCardEvery'):
        _validate_number(frontend, key, 0, 10000)
    _validate_number(frontend, 'preloadScrollPixels', 0, 5000)

    # 防检测随机化字段：总开关与打乱开关为布尔，比率 0～100，延时 0～600000 且上限不小于下限。
    for key in ('antiDetectionEnabled', 'shuffleJobOrder'):
        if not isinstance(frontend.get(key), bool):
            raise ValueError(f'{key} 必须是开关值')
    for key in ('randomSkipRatio', 'randomNoIntroduceRatio'):
        _validate_number(frontend, key, 0, 100)
    for key in ('randomDelayMinMs', 'randomDelayMaxMs'):
        _validate_number(frontend, key, 0, 600000)
    if frontend['randomDelayMaxMs'] < frontend['randomDelayMinMs']:
        raise ValueError('randomDelayMaxMs 不能小于 randomDelayMinMs')
    if not isinstance(frontend.get('hrActiveFilterEnabled'), bool):
        raise ValueError('hrActiveFilterEnabled 必须是开关值')
    if frontend.get('hrActiveMinLevel') not in {'online', 'just_now', 'today', 'within_3_days', 'this_week', 'this_month'}:
        raise ValueError('hrActiveMinLevel 不是有效的活跃档位')

    backend = config['backend']
    _validate_number(backend, 'job_score_delay_base_ms', 0, 600000)
    _validate_number(backend, 'job_score_delay_jitter_ms', 0, 600000)
    _validate_number(backend, 'daily_greet_limit', 1, 150)
    database_name = backend.get('delivery_db_path')
    if (
        not isinstance(database_name, str)
        or not database_name.strip()
        or Path(database_name).name != database_name
    ):
        raise ValueError('delivery_db_path 必须是非空文件名')

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


def get_public_config(effective: dict | None = None) -> dict:
    """重新加载磁盘配置并返回可公开副本、修订号及需重启字段列表。

    调用会刷新 ``Config`` 的进程内有效值，但不会写文件。
    """
    effective = Config.reload() if effective is None else effective
    public = copy.deepcopy(effective)
    return {
        'config': public,
        'revision': config.CONFIG_PATH.stat().st_mtime_ns if config.CONFIG_PATH.exists() else 0,
        'restartRequiredFields': ['backend.delivery_db_path'],
    }


def save_config(payload: dict) -> dict:
    """校验并原子替换 ``user_config.json``，随后热加载并返回最新公开配置。

    ``payload`` 必须含完整 ``config`` 对象。旧版简历内容和 LLM 字段会被丢弃，避免
    敏感接口配置写入普通 JSON；多请求并发保存时应由上层串行化。
    """
    incoming = payload.get('config') if isinstance(payload, dict) else None
    if not isinstance(incoming, dict):
        raise ValueError('缺少 config 对象')
    # 管理页提交完整表单，因此直接替换；若递归合并，已删除的扣星关键词会被旧值恢复。
    merged = copy.deepcopy(incoming)
    merged.pop('resume_content', None)
    # 兼容尚未提交新开关字段的旧版管理页面，升级后默认维持原有行为。
    merged.setdefault('llm_greeting_enabled', config.DEFAULT_USER_CONFIG['llm_greeting_enabled'])
    merged.setdefault('scoring_enabled', config.DEFAULT_USER_CONFIG['scoring_enabled'])
    # 旧版前端可能不提交新增的防检测字段，用默认值补齐后再校验，避免误判为缺字段。
    if isinstance(merged.get('frontend'), dict):
        merged['frontend'].pop('manualFilterWaitMs', None)
        for key, default in config.DEFAULT_USER_CONFIG['frontend'].items():
            merged['frontend'].setdefault(key, default)
    if isinstance(merged.get('tags'), list):
        merged['tags'] = [tag.strip() if isinstance(tag, str) else tag for tag in merged['tags']]
    # 大模型接口已迁移到 .env，绝不写回 user_config.json。
    merged.pop('llm', None)
    for legacy_key in ('think_model', 'chat_model'):
        merged.pop(legacy_key, None)
    validate_config(merged)
    atomic_write_text(config.CONFIG_PATH, json.dumps(merged, ensure_ascii=False, indent=2) + '\n')
    return get_public_config(Config.reload())


def list_resumes() -> dict:
    """列出可管理简历及当前选择；只读取目录和进程内配置。"""
    return list_resume_files(Config.resume_name)


def read_resume(name: str) -> dict:
    """读取指定简历的名称、内容和 UTF-8 字节数；非法路径由存储层拒绝。"""
    return read_resume_file(name)


def select_resume(name: str) -> dict:
    """确认简历存在后持久化当前选择，并返回更新后的简历列表。

    此操作会改写用户配置并热加载 ``Config``，不会修改简历文件本身。
    """
    selected = require_resume_file(name)
    public = get_public_config()['config']
    public['resume_name'] = selected
    save_config({'config': public})
    return list_resumes()


def save_resume(name: str, content: str, select: bool = True) -> dict:
    """保存简历文本并可选设为当前简历，返回保存后的文件信息。

    ``select=True`` 还会持久化用户配置，因此可能产生两次独立的文件替换；调用方应避免
    对同一简历并发保存。
    """
    result = save_resume_file(name, content)
    if select:
        select_resume(name)
        result['selected'] = True
    return result


def get_prompts() -> dict:
    """返回提示词键、界面标签和当前内容；不修改提示词文件。"""
    return {
        'items': [
            {'key': key, 'label': prompts.PROMPT_LABELS[key], 'content': value}
            for key, value in prompts.get_prompt_values().items()
        ]
    }


def save_prompts(values: dict) -> dict:
    """委托提示词模块校验并持久化内容，随后返回完整的最新提示词列表。"""
    prompts.save_prompt_values(values)
    return get_prompts()
