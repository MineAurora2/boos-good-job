"""简历文件目录、名称安全校验、选择解析和原子写入工具。

所有受管理简历必须位于项目 ``resumes`` 目录且使用 ``.md`` 或 ``.txt`` 后缀；示例和
模板名称会被排除。写入采用同目录临时文件替换，但不提供多写者锁，调用层应避免并发
保存同名简历。
"""

from __future__ import annotations

from pathlib import Path
import re

from storage_io import atomic_write_text


ROOT = Path(__file__).resolve().parent
RESUME_DIR = ROOT / 'resumes'
RESUME_SUFFIXES = {'.md', '.txt'}
MAX_RESUME_SIZE = 2 * 1024 * 1024
_EXAMPLE_MARKERS = ('example', 'sample', 'template', '示例', '样例', '模板')


def _ensure_resume_dir() -> None:
    """确保专用简历目录存在；目录缺失时会创建磁盘目录。"""
    RESUME_DIR.mkdir(parents=True, exist_ok=True)


def _is_example_name(name: str) -> bool:
    """判断文件名是否包含约定的中英文示例或模板标记。"""
    lowered = str(name or '').casefold()
    return any(marker in lowered for marker in _EXAMPLE_MARKERS)


def validate_resume_name(name: str) -> str:
    """校验单一简历文件名并返回原名称。

    路径片段、非法字符、不支持后缀及示例/模板名称会抛 ``ValueError``；该函数本身
    不访问或修改文件系统。
    """
    clean_name = Path(name or '').name
    if clean_name != name or not clean_name or not re.fullmatch(r'[\w\-.\u4e00-\u9fff]+', clean_name):
        raise ValueError('简历文件名无效')
    if Path(clean_name).suffix.lower() not in RESUME_SUFFIXES:
        raise ValueError('只允许 .md 或 .txt 简历')
    if _is_example_name(clean_name):
        raise ValueError('示例或模板文件不进入简历管理')
    return clean_name


def _safe_resume_path(name: str) -> Path:
    """解析受管理简历的绝对路径，并再次确认结果未逃逸专用目录。"""
    clean_name = validate_resume_name(name)
    _ensure_resume_dir()
    # 即使未来放宽文件名规则，解析后的父目录检查仍可阻止路径穿越和绝对路径逃逸。
    path = (RESUME_DIR / clean_name).resolve()
    if path.parent != RESUME_DIR.resolve():
        raise ValueError('简历路径无效')
    return path


def _resume_names() -> list[str]:
    """返回按名称排序的可管理简历，不包含目录、其他后缀或示例文件。"""
    _ensure_resume_dir()
    names = []
    for path in RESUME_DIR.iterdir():
        if not path.is_file() or path.suffix.lower() not in RESUME_SUFFIXES:
            continue
        try:
            names.append(validate_resume_name(path.name))
        except ValueError:
            continue
    return sorted(names)


def _resolve_resume_name(configured: str, names: list[str]) -> str:
    """Resolve a configured name against one previously scanned directory listing."""
    configured_name = Path(str(configured or '')).name
    if configured_name == configured and configured_name in names:
        return configured_name
    for preferred in ('resume.md', '简历.md'):
        if preferred in names:
            return preferred
    return names[0] if names else ''


def resolve_resume_name(configured: str = '') -> str:
    """解析当前简历名称，依次采用有效配置、默认名称和首个可用文件。

    目录为空时返回空串；读取目录过程中可能按需创建 ``resumes`` 目录。
    """
    return _resolve_resume_name(configured, _resume_names())


def list_resume_files(configured: str = '') -> dict:
    """返回当前选择、原配置名及简历元数据列表，不读取文件正文。"""
    names = _resume_names()
    selected = _resolve_resume_name(configured, names)
    items = []
    for name in names:
        path = _safe_resume_path(name)
        stat = path.stat()
        items.append({
            'name': name,
            'exists': True,
            'size': stat.st_size,
            'updatedAt': stat.st_mtime,
            'selected': name == selected,
        })
    configured_name = Path(str(configured or '')).name
    if configured_name != configured:
        configured_name = ''
    return {'selected': selected, 'configured': configured_name, 'items': items}


def read_resume_file(name: str) -> dict:
    """读取合法简历并返回名称、正文和 UTF-8 字节数；文件不存在时正文为空。"""
    path = _safe_resume_path(name)
    content = path.read_text(encoding='utf-8') if path.exists() else ''
    return {'name': name, 'content': content, 'size': len(content.encode('utf-8'))}


def save_resume_file(name: str, content: str) -> dict:
    """校验名称和 2 MB 大小上限后原子保存简历，并返回最新文件信息。

    ``content`` 必须为字符串；校验失败抛 ``ValueError``。成功会创建目录或覆盖同名文件。
    """
    path = _safe_resume_path(name)
    if not isinstance(content, str):
        raise ValueError('简历内容必须是文本')
    if len(content.encode('utf-8')) > MAX_RESUME_SIZE:
        raise ValueError('简历文件不能超过 2MB')
    atomic_write_text(path, content)
    return read_resume_file(name)


def require_resume_file(name: str) -> str:
    """确认合法简历是现有普通文件并返回文件名，不存在时抛 ``ValueError``。"""
    path = _safe_resume_path(name)
    if not path.exists() or not path.is_file():
        raise ValueError('简历文件不存在')
    return path.name


def load_resume_selection(configured: str = '') -> tuple[str, str]:
    """返回解析后的当前简历名和正文；无可用简历时返回两个空串。"""
    name = resolve_resume_name(configured)
    if not name:
        return '', ''
    return name, read_resume_file(name)['content']
