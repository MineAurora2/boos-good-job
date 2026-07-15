"""集中管理项目根目录与所有本地数据文件路径。

历史上各模块分别用 ``Path(__file__).resolve().parent`` 反复推导项目根目录；模块迁移到
``app/`` 包后，根目录不再是模块所在目录。这里统一给出 ``PROJECT_ROOT`` 以及运行期读写的
数据文件、目录路径，避免路径推导分散在各处、迁移后失效。
"""

from __future__ import annotations

from pathlib import Path


# app/paths.py -> app/ -> 项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 运行期配置与凭据。
CONFIG_PATH = PROJECT_ROOT / 'user_config.json'
ENV_PATH = PROJECT_ROOT / '.env'
PROMPT_OVERRIDE_PATH = PROJECT_ROOT / 'prompt_overrides.json'
CONTROL_CENTER_STATE_PATH = PROJECT_ROOT / 'control_center_state.json'

# 数据目录。
DASHBOARD_DIR = PROJECT_ROOT / 'dashboard'
RESUME_DIR = PROJECT_ROOT / 'resumes'
