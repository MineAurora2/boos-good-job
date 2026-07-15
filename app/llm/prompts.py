"""Prompt templates used by the two active LLM workflows."""

from __future__ import annotations

import json
from string import Formatter
import threading

from app import paths
from app.storage.io import atomic_write_text


CUSTOM_INTRODUCE = """你负责生成一条可以直接发送给招聘者的首次招呼消息。

<岗位信息>
{job_info}
</岗位信息>

<求职者简历>
{resume}
</求职者简历>

只使用简历中真实存在且与岗位直接相关的经历和技能，不得编造。使用求职者第一人称，语气自然、礼貌、真诚，根据实际情况突出相关工作、实习经验或应届生身份。岗位未说明工作时间时，可以在结尾简短询问；已经说明则不要重复询问。

最终正文为80至130字、单段纯文本，不换行，不使用列表、标题、Markdown、引号或占位变量。在内部完成信息筛选和措辞检查，禁止输出分析、解释、思考步骤、写作计划或草稿说明。只输出可直接发送给招聘者的完整消息。
""".strip()

JOB_FILTER = """判断这份工作是否适合求职者。

工作介绍：
{job_info}

求职者简历：
{resume}

只在以下情况返回false：
- 岗位明确要求简历中没有的技术，并且没有提到简历中相关技术的
- 岗位和不符合简历
- 岗位是纯销售、纯客服、纯管理，与运维/网络/IT技术支持无关
- 岗位明确写了大小周、单休、月休4天
- 岗位要求3年以上工作经验

其他情况都返回true。

输出两行：
第一行：true或false
第二行：给出200字以内的理由
""".strip()


PROMPT_KEYS = ('CUSTOM_INTRODUCE', 'JOB_FILTER')
PROMPT_LABELS = {
    'CUSTOM_INTRODUCE': '定制打招呼语',
    'JOB_FILTER': 'AI 岗位筛选',
}
PROMPT_REQUIRED_FIELDS = {
    'CUSTOM_INTRODUCE': {'resume', 'job_info'},
    'JOB_FILTER': {'resume', 'job_info'},
}
_DEFAULT_PROMPTS = {key: globals()[key] for key in PROMPT_KEYS}
_OVERRIDE_PATH = paths.PROMPT_OVERRIDE_PATH
_SAVE_LOCK = threading.Lock()


def validate_prompt(name: str, content: str) -> None:
    """Validate a managed prompt and its allowed format placeholders."""
    if name not in PROMPT_KEYS:
        raise ValueError(f'不支持的提示词: {name}')
    if not isinstance(content, str) or not content.strip():
        raise ValueError('提示词内容不能为空')
    if len(content) > 50000:
        raise ValueError('单个提示词不能超过 50000 字符')
    try:
        fields = {
            field_name.split('.')[0].split('[')[0]
            for _, field_name, _, _ in Formatter().parse(content)
            if field_name
        }
    except ValueError as error:
        raise ValueError(f'花括号格式错误: {error}') from error
    required = PROMPT_REQUIRED_FIELDS[name]
    missing = required - fields
    if missing:
        raise ValueError(f'缺少必要占位符: {", ".join(sorted(missing))}')
    unknown = fields - required
    if unknown:
        raise ValueError(f'包含不支持的占位符: {", ".join(sorted(unknown))}')
    try:
        content.format(**{field: f'<{field}>' for field in required})
    except (KeyError, ValueError, IndexError) as error:
        raise ValueError(f'提示词格式化失败: {error}') from error


def _load_prompt_overrides() -> dict[str, str]:
    """Read valid active overrides and ignore damaged or retired entries."""
    if not _OVERRIDE_PATH.exists():
        return {}
    try:
        data = json.loads(_OVERRIDE_PATH.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value.strip()
        for key, value in data.items()
        if key in PROMPT_KEYS and isinstance(value, str) and value.strip()
    }


def reload_prompt_overrides() -> dict[str, str]:
    """Merge built-in prompts with disk overrides and publish effective values."""
    effective = dict(_DEFAULT_PROMPTS)
    effective.update(_load_prompt_overrides())
    globals().update(effective)
    return effective


def get_prompt_values() -> dict[str, str]:
    """Return all effective prompts in stable display order."""
    return {key: globals()[key] for key in PROMPT_KEYS}


def _save_prompt_values(values: dict[str, str]) -> dict[str, str]:
    """Persist changed prompts only, then reload the effective set."""
    if not isinstance(values, dict):
        raise ValueError('提示词数据必须是对象')
    overrides = _load_prompt_overrides()
    for name, content in values.items():
        validate_prompt(name, content)
        normalized = content.strip()
        if normalized == _DEFAULT_PROMPTS[name]:
            overrides.pop(name, None)
        else:
            overrides[name] = normalized
    atomic_write_text(
        _OVERRIDE_PATH,
        json.dumps(overrides, ensure_ascii=False, indent=2) + '\n',
    )
    return reload_prompt_overrides()


def save_prompt_values(values: dict[str, str]) -> dict[str, str]:
    """Serialize partial prompt updates so concurrent edits cannot overwrite each other."""
    with _SAVE_LOCK:
        return _save_prompt_values(values)


reload_prompt_overrides()
