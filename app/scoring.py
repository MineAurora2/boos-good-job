"""岗位文本解析与纯规则扣星评分。"""

from __future__ import annotations

import re
import unicodedata

from app.config import Config


_FIELD_HEADING = re.compile(
    r'(?im)^[ \t]*#{0,6}[ \t]*'
    r'(职位名称|岗位名称|薪资范围|薪资|职位描述|岗位描述)'
    r'[ \t]*[：:]?[ \t]*$',
)
_FIELD_NAMES = {
    '职位名称': 'title',
    '岗位名称': 'title',
    '薪资范围': 'salary',
    '薪资': 'salary',
    '职位描述': 'detail',
    '岗位描述': 'detail',
}


def parse_job_fields(job: str) -> tuple[str, str, str]:
    """从脚本上传的岗位文本中提取标题、薪资和职位描述。"""
    text = str(job or '').strip()
    if not text:
        return '', '', ''

    matches = list(_FIELD_HEADING.finditer(text))
    if matches:
        fields = {'title': '', 'salary': '', 'detail': ''}
        for index, match in enumerate(matches):
            value_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            field_name = _FIELD_NAMES[match.group(1)]
            fields[field_name] = text[match.end():value_end].strip()
        return fields['title'], fields['salary'], fields['detail']

    # 兼容没有标准标题、但仍按三个空行分段上传的旧文本。
    sections = [section.strip() for section in re.split(r'\n\s*\n', text) if section.strip()]
    title = ''
    salary = ''
    detail = text
    if sections:
        title_lines = sections[0].splitlines()
        if len(title_lines) > 1:
            title = '\n'.join(title_lines[1:]).strip()
    if len(sections) >= 2:
        salary_lines = sections[1].splitlines()
        if len(salary_lines) > 1:
            salary = '\n'.join(salary_lines[1:]).strip()
    if len(sections) >= 3:
        detail_lines = sections[2].splitlines()
        if len(detail_lines) > 1:
            detail = '\n'.join(detail_lines[1:]).strip()
    return title, salary, detail


def _normalize_match_text(value: object) -> str:
    """Normalize compatibility characters, case and whitespace for rule matching."""
    normalized = unicodedata.normalize('NFKC', str(value or '')).casefold()
    return re.sub(r'\s+', ' ', normalized).strip()


def _keyword_span(text: str, keyword: str) -> tuple[int, int] | None:
    if not keyword:
        return None
    # Keywords made entirely from ASCII words use boundaries so ``java`` does
    # not match ``javascript``. Chinese and other scripts retain substring
    # semantics, which is what users expect from compact job titles.
    if keyword.isascii() and re.search(r'[a-z0-9]', keyword):
        match = re.search(
            rf'(?<![a-z0-9_]){re.escape(keyword)}(?![a-z0-9_])',
            text,
        )
        return match.span() if match else None
    start = text.find(keyword)
    return (start, start + len(keyword)) if start >= 0 else None


def _find_matches(text: str, keyword_scores: dict[str, int]) -> list[tuple[str, int]]:
    """Return every distinct keyword deduction, at most once per field."""
    normalized = _normalize_match_text(text)
    candidates = []
    seen_needles = set()
    for keyword, score in keyword_scores.items():
        needle = _normalize_match_text(keyword)
        if not needle or needle in seen_needles:
            continue
        seen_needles.add(needle)
        span = _keyword_span(normalized, needle)
        if span:
            candidates.append((*span, keyword, score))

    return [(keyword, score) for _start, _end, keyword, score in candidates]


def _unscored_result(title: str, salary: str, detail: str) -> dict:
    """扣分规则关闭时的结果：所有岗位保持 5 星、不做任何扣分筛选。"""
    return {
        'title': title,
        'salary': salary,
        'detail': detail,
        'matched_field': 'none',
        'keyword': None,
        'score': 100,
        'stars': 5,
        'rawStars': 5,
        'deductedStars': 0,
        'discarded': False,
        'deductions': [],
        'scoringEnabled': False,
        'reason': '扣分规则已关闭，保持5星',
    }


def evaluate_job_match(job: str) -> dict:
    """从五星开始应用关键词扣星规则，并返回完整评分明细。"""
    title, salary, detail = parse_job_fields(job)
    if not Config.scoring_enabled:
        return _unscored_result(title, salary, detail)
    title_matches = _find_matches(title, Config.title_deduction_keywords)
    detail_matches = _find_matches(detail, Config.detail_deduction_keywords)
    title_deduction = sum(stars for _, stars in title_matches)
    detail_deduction = sum(stars for _, stars in detail_matches)
    deducted_stars = title_deduction + detail_deduction
    raw_stars = 5 - deducted_stars
    stars = max(0, min(5, raw_stars))
    discarded = raw_stars < 0
    final_score = stars * 20
    all_matches = title_matches + detail_matches
    deductions = [
        {'field': 'title', 'fieldLabel': '职位名称', 'keyword': keyword, 'deductStars': value}
        for keyword, value in title_matches
    ] + [
        {'field': 'detail', 'fieldLabel': '职位描述', 'keyword': keyword, 'deductStars': value}
        for keyword, value in detail_matches
    ]
    keyword = max(all_matches, key=lambda item: item[1])[0] if all_matches else None
    matched_field = 'title' if title_matches else ('detail' if detail_matches else 'none')
    reason = f'初始5星，命中规则共扣{deducted_stars}星' if all_matches else '未命中扣星规则，保持5星'
    return {
        'title': title,
        'salary': salary,
        'detail': detail,
        'matched_field': matched_field,
        'keyword': keyword,
        'score': final_score,
        'stars': stars,
        'rawStars': raw_stars,
        'deductedStars': deducted_stars,
        'discarded': discarded,
        'deductions': deductions,
        'scoringEnabled': True,
        'reason': reason,
    }


__all__ = ['evaluate_job_match', 'parse_job_fields']
