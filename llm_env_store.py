"""读取与写入 .env 中的 GOODJOB_LLM_* 大模型接口配置。

.env 是多个大模型接口的唯一存储位置，采用编号变量：

    GOODJOB_LLM_STRATEGY=failover        # failover 故障转移 | round_robin 轮询
    GOODJOB_LLM_JOB_FILTER=false         # AI 二次筛选开关
    GOODJOB_LLM_TIMEOUT=180              # 全局请求超时（秒）
    GOODJOB_LLM_1_NAME=SenseNova
    GOODJOB_LLM_1_API_BASE=https://token.sensenova.cn/v1
    GOODJOB_LLM_1_API_KEY=sk-****
    GOODJOB_LLM_1_MODEL=deepseek-v4-flash
    GOODJOB_LLM_1_PROXY_URL=http://127.0.0.1:7890
    GOODJOB_LLM_1_PROXY_ENABLED=false
    GOODJOB_LLM_1_ENABLED=true

旧格式 GOODJOB_LLM_API_BASE / GOODJOB_LLM_API_KEY（无编号）会被当作 1 号接口
读取，首次保存后自动迁移为编号格式。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / '.env'

STRATEGIES = ('failover', 'round_robin')
DEFAULT_STRATEGY = 'failover'
DEFAULT_TIMEOUT = 180
MAX_PROVIDERS = 20

# 写入时用这个哨兵表示“保留原有 api_key，不修改”。读出的配置永远脱敏，
# 因此前端不修改某个 key 时会回传哨兵，避免把脱敏占位写回 .env。
KEEP_SECRET = '__KEEP__'

_TRUE_VALUES = {'1', 'true', 'yes', 'on'}
_PROVIDER_RE = re.compile(
    r'^GOODJOB_LLM_(\d+)_(NAME|API_BASE|API_KEY|MODEL|PROXY_URL|PROXY_ENABLED|ENABLED)$'
)


def _unquote(value: str) -> str:
    """解析 .env 中的单双引号值；非法 JSON 双引号值退回普通字符串。"""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value


def _quote(value: str) -> str:
    """仅在 .env 语法需要时给值加双引号并完成转义。"""
    text = str(value if value is not None else '')
    if text == '' or re.search(r'[\s#"\'=]', text):
        return json.dumps(text, ensure_ascii=False)
    return text


def _to_bool(value, default: bool = True) -> bool:
    """把网页布尔值和常见环境变量写法统一转换为 bool。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in _TRUE_VALUES


def _read_lines() -> list[str]:
    """按行读取 .env，文件不存在时视为空配置。"""
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding='utf-8').splitlines()


def _parse_pairs(lines: list[str]) -> dict[str, str]:
    """解析有效的 KEY=VALUE 行，同时跳过注释、空行和未知文本。"""
    pairs: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        pairs[key.strip()] = _unquote(value)
    return pairs


def _mask(secret: str) -> str:
    """脱敏 api_key，只保留头尾几位供人工核对。"""
    text = str(secret or '')
    if not text:
        return ''
    if len(text) <= 8:
        return text[0] + '*' * (len(text) - 1) if len(text) > 1 else '*'
    return f'{text[:4]}{"*" * 6}{text[-4:]}'


def _mask_proxy_url(proxy_url: str) -> str:
    """保留代理主机信息，但隐藏 URL userinfo 中的密码。"""
    text = str(proxy_url or '').strip()
    if not text:
        return ''
    try:
        parsed = urlsplit(text)
    except ValueError:
        return '******'
    if '@' not in parsed.netloc:
        return text
    _, host = parsed.netloc.rsplit('@', 1)
    netloc = f'******@{host}'
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _validate_proxy_url(proxy_url: str, provider_name: str) -> None:
    """限制代理为 http(s) URL，并拒绝容易混淆的查询参数和片段。"""
    try:
        parsed = urlsplit(proxy_url)
        port = parsed.port
    except ValueError as error:
        raise ValueError(f'{provider_name} 的代理地址无效：{error}') from error
    if parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname:
        raise ValueError(f'{provider_name} 的代理地址必须是有效的 HTTP(S) 地址')
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f'{provider_name} 的代理端口必须在 1 到 65535 之间')
    if parsed.query or parsed.fragment:
        raise ValueError(f'{provider_name} 的代理地址不能包含查询参数或片段')


def _load_raw() -> dict:
    """从 .env 与进程环境合并出结构化配置（api_key 为明文，仅供后端内部使用）。"""
    pairs = _parse_pairs(_read_lines())
    # 进程环境优先级更高（例如显式导出的变量），与 config.py 的加载语义一致。
    merged = {**pairs}
    for key in os.environ:
        if key.startswith('GOODJOB_LLM'):
            merged[key] = os.environ[key]

    strategy = str(merged.get('GOODJOB_LLM_STRATEGY') or DEFAULT_STRATEGY).strip().lower()
    if strategy not in STRATEGIES:
        strategy = DEFAULT_STRATEGY
    try:
        timeout = max(1, min(600, int(float(merged.get('GOODJOB_LLM_TIMEOUT') or DEFAULT_TIMEOUT))))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    job_filter = _to_bool(merged.get('GOODJOB_LLM_JOB_FILTER'), False)

    providers: dict[int, dict] = {}
    for key, value in merged.items():
        match = _PROVIDER_RE.match(key)
        if not match:
            continue
        index = int(match.group(1))
        if index < 1 or index > MAX_PROVIDERS:
            continue
        field = match.group(2).lower()
        providers.setdefault(index, {})[field] = value

    # 兼容旧的无编号变量，视为 1 号接口（仅在没有 1 号编号配置时生效）。
    legacy_base = merged.get('GOODJOB_LLM_API_BASE')
    legacy_key = merged.get('GOODJOB_LLM_API_KEY')
    if (legacy_base or legacy_key) and 1 not in providers:
        providers[1] = {
            'name': 'default',
            'api_base': legacy_base or '',
            'api_key': legacy_key or '',
            'model': merged.get('GOODJOB_LLM_MODEL') or '',
            'proxy_url': merged.get('GOODJOB_LLM_PROXY_URL') or '',
            'proxy_enabled': merged.get('GOODJOB_LLM_PROXY_ENABLED'),
        }

    ordered = []
    for index in sorted(providers):
        item = providers[index]
        proxy_url = str(item.get('proxy_url') or '').strip()
        ordered.append({
            'index': index,
            'name': str(item.get('name') or f'接口{index}').strip(),
            'api_base': str(item.get('api_base') or '').strip(),
            'api_key': str(item.get('api_key') or '').strip(),
            'model': str(item.get('model') or '').strip(),
            'proxy_url': proxy_url,
            'proxy_enabled': _to_bool(item.get('proxy_enabled'), bool(proxy_url)),
            'enabled': _to_bool(item.get('enabled'), True),
        })
    return {
        'strategy': strategy,
        'timeout': timeout,
        'jobFilter': job_filter,
        'providers': ordered,
    }


def load_llm_config() -> dict:
    """后端内部使用：返回含明文 api_key 的完整配置。"""
    return _load_raw()


def public_llm_config() -> dict:
    """前端展示使用：api_key 脱敏，并额外给出是否已配置的布尔标记。"""
    raw = _load_raw()
    providers = []
    for item in raw['providers']:
        providers.append({
            'index': item['index'],
            'name': item['name'],
            'api_base': item['api_base'],
            'model': item['model'],
            'enabled': item['enabled'],
            'apiKeyMasked': _mask(item['api_key']),
            'apiKeyConfigured': bool(item['api_key']),
            'proxyEnabled': item['proxy_enabled'],
            'proxyUrlMasked': _mask_proxy_url(item['proxy_url']),
            'proxyUrlConfigured': bool(item['proxy_url']),
        })
    return {
        'strategy': raw['strategy'],
        'timeout': raw['timeout'],
        'jobFilter': raw['jobFilter'],
        'providers': providers,
    }


def _validate_incoming(payload: dict) -> dict:
    """校验管理面板提交内容，并把保留哨兵还原为已保存的敏感值。"""
    if not isinstance(payload, dict):
        raise ValueError('接口配置必须是对象')
    strategy = str(payload.get('strategy') or DEFAULT_STRATEGY).strip().lower()
    if strategy not in STRATEGIES:
        raise ValueError('调度策略只能是 failover 或 round_robin')
    try:
        timeout = int(float(payload.get('timeout', DEFAULT_TIMEOUT)))
    except (TypeError, ValueError):
        raise ValueError('全局超时必须是数字')
    if not 1 <= timeout <= 600:
        raise ValueError('全局超时必须在 1 到 600 秒之间')
    job_filter = _to_bool(payload.get('jobFilter'), False)

    incoming = payload.get('providers')
    if not isinstance(incoming, list):
        raise ValueError('providers 必须是数组')
    if len(incoming) > MAX_PROVIDERS:
        raise ValueError(f'最多支持 {MAX_PROVIDERS} 个接口')

    # 前端只能看到脱敏结果，因此修改其他字段时会提交 KEEP_SECRET。这里必须按
    # 原编号取回明文，不能把星号占位符或哨兵本身写入配置文件。
    existing = {item['index']: item for item in _load_raw()['providers']}
    cleaned = []
    for order, item in enumerate(incoming, start=1):
        if not isinstance(item, dict):
            raise ValueError('每个接口必须是对象')
        name = str(item.get('name') or f'接口{order}').strip()[:60]
        api_base = str(item.get('api_base') or '').strip()
        model = str(item.get('model') or '').strip()[:120]
        enabled = _to_bool(item.get('enabled'), True)
        if api_base:
            if not re.match(r'^https?://[^\s]+$', api_base):
                raise ValueError(f'{name} 的接口地址必须是有效的 HTTP(S) 地址')
        api_key_input = item.get('api_key')
        old_index = item.get('index')
        if api_key_input == KEEP_SECRET or api_key_input is None:
            # 保留旧 key：按原编号回填明文。
            api_key = existing.get(old_index, {}).get('api_key', '') if isinstance(old_index, int) else ''
        else:
            api_key = str(api_key_input).strip()
        proxy_url_input = item.get('proxy_url')
        if proxy_url_input == KEEP_SECRET or proxy_url_input is None:
            # 代理地址可能带认证信息，与 API Key 一样按原编号保留。
            proxy_url = existing.get(old_index, {}).get('proxy_url', '') if isinstance(old_index, int) else ''
        else:
            proxy_url = str(proxy_url_input).strip()
        if 'proxy_enabled' in item:
            proxy_enabled = _to_bool(item.get('proxy_enabled'), bool(proxy_url))
        elif isinstance(old_index, int) and old_index in existing:
            # 兼容尚未提交代理字段的旧版前端，不能在一次普通保存后误开启代理。
            proxy_enabled = existing[old_index].get('proxy_enabled', False)
        else:
            proxy_enabled = bool(proxy_url)
        if proxy_url:
            _validate_proxy_url(proxy_url, name)
        if enabled and proxy_enabled and not proxy_url:
            raise ValueError(f'{name} 已启用代理，但代理地址为空')
        if enabled and (not api_base or not model or not api_key):
            raise ValueError(f'{name} 已启用，但接口地址、模型名称或 API Key 不完整')
        cleaned.append({
            'name': name,
            'api_base': api_base,
            'api_key': api_key,
            'model': model,
            'proxy_url': proxy_url,
            'proxy_enabled': proxy_enabled,
            'enabled': enabled,
        })
    return {'strategy': strategy, 'timeout': timeout, 'jobFilter': job_filter, 'providers': cleaned}


def resolve_provider_config(payload: dict) -> dict:
    """校验一张未保存的接口卡片，并解析需要保留的敏感字段，供测活使用。"""
    if not isinstance(payload, dict):
        raise ValueError('待测试的接口配置必须是对象')
    current = _load_raw()
    config = _validate_incoming({
        'strategy': current['strategy'],
        'timeout': current['timeout'],
        'jobFilter': current['jobFilter'],
        'providers': [{**payload, 'enabled': True}],
    })
    provider = config['providers'][0]
    provider['index'] = payload.get('index') if isinstance(payload.get('index'), int) else 0
    return provider


def save_llm_config(payload: dict) -> dict:
    """校验并写回 .env，保留所有非 GOODJOB_LLM_* 行；返回脱敏后的公开配置。"""
    config = _validate_incoming(payload)

    # 保留 .env 中所有与大模型无关的行（注释、空行、其他变量）。
    preserved = []
    for raw in _read_lines():
        stripped = raw.strip()
        if not stripped or stripped.startswith('#'):
            preserved.append(raw)
            continue
        if '=' not in stripped:
            preserved.append(raw)
            continue
        key = stripped.split('=', 1)[0].strip()
        if key.startswith('GOODJOB_LLM'):
            continue  # 丢弃旧的大模型变量（含无编号兼容变量），稍后统一重写
        preserved.append(raw)

    lines = list(preserved)
    if lines and lines[-1].strip() != '':
        lines.append('')
    lines.append('# 大模型接口配置（由网页面板管理，请勿提交真实 API Key）')
    lines.append(f'GOODJOB_LLM_STRATEGY={config["strategy"]}')
    lines.append(f'GOODJOB_LLM_JOB_FILTER={"true" if config["jobFilter"] else "false"}')
    lines.append(f'GOODJOB_LLM_TIMEOUT={config["timeout"]}')
    for index, provider in enumerate(config['providers'], start=1):
        lines.append('')
        lines.append(f'GOODJOB_LLM_{index}_NAME={_quote(provider["name"])}')
        lines.append(f'GOODJOB_LLM_{index}_API_BASE={_quote(provider["api_base"])}')
        lines.append(f'GOODJOB_LLM_{index}_API_KEY={_quote(provider["api_key"])}')
        lines.append(f'GOODJOB_LLM_{index}_MODEL={_quote(provider["model"])}')
        lines.append(f'GOODJOB_LLM_{index}_PROXY_URL={_quote(provider["proxy_url"])}')
        lines.append(f'GOODJOB_LLM_{index}_PROXY_ENABLED={"true" if provider["proxy_enabled"] else "false"}')
        lines.append(f'GOODJOB_LLM_{index}_ENABLED={"true" if provider["enabled"] else "false"}')

    content = '\n'.join(lines).rstrip('\n') + '\n'
    temp_path = ENV_PATH.with_name('.env.tmp')
    # 先完整写入并刷盘，再以原子替换覆盖 .env，避免进程中断留下半份配置。
    with temp_path.open('w', encoding='utf-8', newline='\n') as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, ENV_PATH)

    _sync_environ(config)
    return public_llm_config()


def _sync_environ(config: dict) -> None:
    """同步当前进程环境，使保存后的配置无需重启即可由管理器重新加载。"""
    # 先整体清理可避免删除 provider 后，旧编号变量继续残留并被 _load_raw 读到。
    for key in [key for key in os.environ if key.startswith('GOODJOB_LLM')]:
        os.environ.pop(key, None)
    os.environ['GOODJOB_LLM_STRATEGY'] = config['strategy']
    os.environ['GOODJOB_LLM_JOB_FILTER'] = 'true' if config['jobFilter'] else 'false'
    os.environ['GOODJOB_LLM_TIMEOUT'] = str(config['timeout'])
    for index, provider in enumerate(config['providers'], start=1):
        os.environ[f'GOODJOB_LLM_{index}_NAME'] = provider['name']
        os.environ[f'GOODJOB_LLM_{index}_API_BASE'] = provider['api_base']
        os.environ[f'GOODJOB_LLM_{index}_API_KEY'] = provider['api_key']
        os.environ[f'GOODJOB_LLM_{index}_MODEL'] = provider['model']
        os.environ[f'GOODJOB_LLM_{index}_PROXY_URL'] = provider['proxy_url']
        os.environ[f'GOODJOB_LLM_{index}_PROXY_ENABLED'] = 'true' if provider['proxy_enabled'] else 'false'
        os.environ[f'GOODJOB_LLM_{index}_ENABLED'] = 'true' if provider['enabled'] else 'false'
