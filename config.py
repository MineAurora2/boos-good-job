"""Project defaults, legacy configuration migration, and runtime reloads."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / 'user_config.json'


def _load_env_file(path: Path) -> None:
    """Load a local ``.env`` without overriding explicit process variables."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] == '"':
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value[1:-1]
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        os.environ[key] = value


_load_env_file(ROOT / '.env')


DEFAULT_USER_CONFIG = {
    'resume_name': 'resume.md',
    # 开启时按岗位调用 LLM 生成招呼语；关闭时直接使用 introduce 固定文本。
    'llm_greeting_enabled': True,
    'introduce': '您好，我是一名对 AI 应用开发、自动化流程和工程落地感兴趣的求职者，想进一步了解这个岗位。',
    'character': '简洁 直接 礼貌',
    'tags': ['运维开发', 'SRE', 'DevOps', '运维工程师', '平台工程师', 'AI应用', 'AI应用工程师', 'AI开发', 'AI产品经理'],
    'backend': {
        'job_score_delay_base_ms': 4000,
        'job_score_delay_jitter_ms': 500,
        'daily_greet_limit': 90,
        'delivery_db_path': 'delivery_state.db',
    },
    'frontend': {
        'serverHost': 'http://127.0.0.1:47999',
        'resumeIndex': 0,
        'thread': 50,
        'timestampTimeout': 3000,
        'onlyGreet': False,
        'manualFilterWaitMs': 10000,
        'roundRestartDelayMs': 2000,
        'maxEmptyRounds': 3,
        'detailTimeout': 10000,
        'greetTimeout': 12000,
        'preloadScrollPixels': 180,
        'preloadScrollWaitMs': 450,
        'preloadStableRoundsLimit': 24,
        'preloadMaxRounds': 300,
        'preloadActivateCardEvery': 0,
        'preloadActivateCardWaitMs': 250,
    },
    'scoring': {
        'title_deduction_keywords': {
            'java': 2,
            '前端': 3,
            '后端': 1,
            '全栈': 1,
            '测试': 5,
            '销售': 5,
            '商务': 5,
            '运营': 5,
            '客服': 5,
            '管培生': 5,
            '培训生': 5,
            '储备干部': 5,
            '储干': 5,
            '项目经理': 5,
            '项目管理': 5,
            '数据开发': 5,
            '数据治理': 5,
            '算法': 5,
            '算法工程师': 5,
            '算法研究员': 5,
            '机器学习算法': 5,
            '深度学习算法': 5,
            '推荐算法': 5,
            '搜索算法': 5,
            'cv算法': 5,
            'nlp算法': 5,
            '多模态算法': 5,
            '模型训练': 5,
            '模型研发': 5,
            '大模型算法': 5,
            '训练': 5,
            '预训练': 5,
            '微调': 5,
            '嵌入式': 5,
            '硬件': 5,
            '渠道': 5,
            '光伏': 5,
        },
        'detail_deduction_keywords': {
            'spring': 1,
            'spring boot': 1,
            'react': 1,
            'vue': 1,
            'android': 1,
            'ios': 1,
            '小程序': 1,
            '客户': 1,
            '渠道': 1,
            '销售': 1,
            '新能源': 1,
            '光伏': 1,
            'to b': 1,
            'to c': 1,
        },
    },
}


def _deduction_rules(values: dict) -> dict[str, int]:
    """Convert negative legacy percentages to bounded one-to-five star deductions."""
    result: dict[str, int] = {}
    for keyword, score in values.items():
        if not isinstance(score, (int, float)) or isinstance(score, bool) or score >= 0:
            continue
        result[keyword] = max(1, min(5, (int(abs(score)) + 19) // 20))
    return result


def _negative_groups(scoring: dict, *names: str) -> dict:
    """Merge legacy groups as negative values, keeping the strongest duplicate."""
    merged: dict = {}
    for name in names:
        values = scoring.get(name)
        if not isinstance(values, dict):
            continue
        for keyword, score in values.items():
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                continue
            value = -abs(score)
            merged[keyword] = min(merged.get(keyword, value), value)
    return merged


def _unify_scoring_rules(scoring: dict, policy: dict | None = None) -> dict:
    """Normalize current rules or migrate supported legacy percentage groups."""
    del policy  # The old scaling policy affected positive scores, which are no longer used.
    current_keys = ('title_deduction_keywords', 'detail_deduction_keywords')
    if any(key in scoring for key in current_keys):
        return {
            key: copy.deepcopy(scoring.get(key, {})) if isinstance(scoring.get(key, {}), dict) else {}
            for key in current_keys
        }

    title_values = scoring.get('title_keywords')
    if not isinstance(title_values, dict):
        title_values = _negative_groups(
            scoring,
            'title_penalty_keywords',
            'title_block_keywords',
            'title_negative_keywords',
        )
    detail_values = scoring.get('detail_keywords')
    if not isinstance(detail_values, dict):
        detail_values = _negative_groups(scoring, 'detail_negative_keywords')
    return {
        'title_deduction_keywords': _deduction_rules(title_values),
        'detail_deduction_keywords': _deduction_rules(detail_values),
    }


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge dictionaries while ignoring explicit ``None`` values."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        elif value is not None:
            result[key] = value
    return result


def _apply_legacy_compat(config: dict, user_config: dict) -> dict:
    """Move still-supported legacy top-level settings into their current groups."""
    legacy_fields = {
        'job_score_delay_base_ms': ('backend', 'job_score_delay_base_ms'),
        'job_score_delay_jitter_ms': ('backend', 'job_score_delay_jitter_ms'),
        'thread': ('frontend', 'thread'),
    }
    for old_key, (group, new_key) in legacy_fields.items():
        if old_key in user_config and user_config[old_key] is not None:
            config[group][new_key] = user_config[old_key]
    return config


def _load_raw_user_config() -> dict:
    """Read the user file, falling back to an empty override when it is unavailable."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_user_config() -> dict:
    """Load defaults, migrate legacy fields, and apply the complete user override."""
    config = copy.deepcopy(DEFAULT_USER_CONFIG)
    user_config = copy.deepcopy(_load_raw_user_config())
    if not user_config:
        return config

    user_config.pop('resume_content', None)
    if isinstance(user_config.get('scoring'), dict):
        user_config['scoring'] = _unify_scoring_rules(
            user_config['scoring'],
            user_config.pop('scoring_policy', None),
        )
    config = _deep_merge(config, user_config)
    # Scoring dictionaries are complete user-managed sets. Restoring deleted defaults here
    # would make removed dashboard cards reappear after a reload.
    if isinstance(user_config.get('scoring'), dict):
        for group_name, keyword_scores in user_config['scoring'].items():
            if isinstance(keyword_scores, dict):
                config['scoring'][group_name] = copy.deepcopy(keyword_scores)
    return _apply_legacy_compat(config, user_config)


USER_CONFIG = load_user_config()


class Config:
    """Process-wide view of the currently effective configuration."""

    resume_name = USER_CONFIG['resume_name']
    llm_greeting_enabled = USER_CONFIG['llm_greeting_enabled']
    introduce = USER_CONFIG['introduce']
    character = USER_CONFIG['character']
    tags = USER_CONFIG['tags']
    job_score_delay_base_ms = USER_CONFIG['backend']['job_score_delay_base_ms']
    job_score_delay_jitter_ms = USER_CONFIG['backend']['job_score_delay_jitter_ms']
    title_deduction_keywords = USER_CONFIG['scoring']['title_deduction_keywords']
    detail_deduction_keywords = USER_CONFIG['scoring']['detail_deduction_keywords']
    frontend = USER_CONFIG['frontend']
    backend = USER_CONFIG['backend']
    scoring = USER_CONFIG['scoring']

    @classmethod
    def get_client_config(cls) -> dict:
        """Return the subset that the browser automation client may read."""
        return {
            'introduce': cls.introduce,
            'character': cls.character,
            'tags': cls.tags,
            'frontend': cls.frontend,
        }

    @classmethod
    def reload(cls) -> dict:
        """Reload disk configuration and replace all runtime attributes together."""
        global USER_CONFIG
        USER_CONFIG = load_user_config()
        cls.resume_name = USER_CONFIG['resume_name']
        cls.llm_greeting_enabled = USER_CONFIG['llm_greeting_enabled']
        cls.introduce = USER_CONFIG['introduce']
        cls.character = USER_CONFIG['character']
        cls.tags = USER_CONFIG['tags']
        cls.job_score_delay_base_ms = USER_CONFIG['backend']['job_score_delay_base_ms']
        cls.job_score_delay_jitter_ms = USER_CONFIG['backend']['job_score_delay_jitter_ms']
        cls.title_deduction_keywords = USER_CONFIG['scoring']['title_deduction_keywords']
        cls.detail_deduction_keywords = USER_CONFIG['scoring']['detail_deduction_keywords']
        cls.frontend = USER_CONFIG['frontend']
        cls.backend = USER_CONFIG['backend']
        cls.scoring = USER_CONFIG['scoring']
        return copy.deepcopy(USER_CONFIG)
