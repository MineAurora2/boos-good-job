from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'user_config.json'
ENV_PATH = ROOT / '.env'
SECRET_FIELDS = {
    'api_base': 'GOODJOB_LLM_API_BASE',
    'api_key': 'GOODJOB_LLM_API_KEY',
}


def _read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            continue
        key, value = stripped.split('=', 1)
        values[key.strip()] = value.strip()
    return lines, values


def _set_env_values(lines: list[str], updates: dict[str, str]) -> list[str]:
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in stripped:
            key = stripped.split('=', 1)[0].strip()
            if key in remaining:
                output.append(f'{key}={json.dumps(remaining.pop(key), ensure_ascii=False)}')
                continue
        output.append(line)
    if remaining and output and output[-1].strip():
        output.append('')
    for key, value in remaining.items():
        output.append(f'{key}={json.dumps(value, ensure_ascii=False)}')
    return output


def main() -> int:
    if not CONFIG_PATH.exists():
        print('user_config.json 不存在，无需迁移。')
        return 0

    config = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    llm = config.get('llm')
    if not isinstance(llm, dict):
        print('user_config.json 没有 llm 配置，无需迁移。')
        return 0

    lines, existing = _read_env(ENV_PATH)
    updates: dict[str, str] = {}
    migrated: list[str] = []
    for json_key, env_key in SECRET_FIELDS.items():
        value = str(llm.get(json_key) or '').strip()
        existing_value = existing.get(env_key, '').strip().strip('"').strip("'")
        if value and not existing_value:
            updates[env_key] = value
            migrated.append(env_key)
        if value:
            llm[json_key] = ''

    if updates:
        ENV_PATH.write_text('\n'.join(_set_env_values(lines, updates)) + '\n', encoding='utf-8')
    if migrated:
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('已迁移并清空 user_config.json 中的字段：' + ', '.join(migrated))
    else:
        print('未发现需要迁移的非空敏感字段。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

