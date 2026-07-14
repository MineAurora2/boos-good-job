import prompts
from config import Config
from llm_gateway import LLMGatewayError, _response_text
from llm_manager import LLM_MANAGER
import json
import copy
from pathlib import Path
import re
import threading
import time


_LLM_LOG_LOCK = threading.Lock()
_LLM_LAST_LOGGED_AT: dict[str, float] = {}


def _log_llm_fallback(operation: str, error: Exception, fallback: str) -> None:
    status_code = error.status_code if isinstance(error, LLMGatewayError) else None
    category = 'circuit' if isinstance(error, LLMGatewayError) and error.circuit_open else str(status_code or type(error).__name__)
    key = f'{operation}:{category}'
    now = time.monotonic()
    with _LLM_LOG_LOCK:
        last_logged = _LLM_LAST_LOGGED_AT.get(key, 0.0)
        if now - last_logged < 30:
            return
        _LLM_LAST_LOGGED_AT[key] = now
    summary = str(error).replace('\n', ' ')[:400]
    print(f'[LLM] {operation}失败，{fallback}: {summary}', flush=True)


def __extract_job_fields(job: str) -> tuple[str, str]:
    """从脚本上传的文本中提取岗位名称和职位描述。"""
    sections = [section.strip() for section in re.split(r'\n\s*\n', job) if section.strip()]
    title = ''
    detail = job.strip()
    if sections:
        title_lines = sections[0].splitlines()
        if len(title_lines) > 1:
            title = '\n'.join(title_lines[1:]).strip()
    if len(sections) >= 3:
        detail_lines = sections[2].splitlines()
        if len(detail_lines) > 1:
            detail = '\n'.join(detail_lines[1:]).strip()
    return title, detail


def __normalize_text(text: str) -> str:
    return text.lower()


def __find_matches(text: str, keyword_scores: dict[str, int]) -> list[tuple[str, int]]:
    normalized = __normalize_text(text)
    candidates = []
    for keyword, score in keyword_scores.items():
        needle = keyword.lower()
        start = normalized.find(needle)
        while needle and start >= 0:
            candidates.append((start, start + len(needle), keyword, score))
            start = normalized.find(needle, start + 1)
    # Prefer the most specific (longest) keyword and do not score overlapping
    # text twice, e.g. "系统运维工程师" no longer also counts "系统"/"运维".
    accepted = []
    occupied = []
    for start, end, keyword, score in sorted(candidates, key=lambda item: (-(item[1] - item[0]), item[0])):
        if any(start < used_end and end > used_start for used_start, used_end in occupied):
            continue
        occupied.append((start, end))
        accepted.append((keyword, score))
    return accepted


def evaluateJobMatch(job: str):
    """Start at five stars and apply deduction-only keyword rules."""
    title, detail = __extract_job_fields(job)
    title_matches = __find_matches(title, Config.title_deduction_keywords)
    detail_matches = __find_matches(detail, Config.detail_deduction_keywords)
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
        'title': title, 'detail': detail, 'matched_field': matched_field, 'keyword': keyword,
        'score': final_score, 'stars': stars, 'rawStars': raw_stars,
        'deductedStars': deducted_stars, 'discarded': discarded, 'blocked': discarded,
        'title_score': 0, 'detail_score': 0,
        'title_penalty_score': title_deduction * 20,
        'penalty_score': detail_deduction * 20,
        'combo_score': 0, 'final_score': final_score, 'title_match_level': 'deduction',
        'title_matches': [],
        'title_penalty_matches': [keyword for keyword, _ in title_matches],
        'detail_infra_matches': [],
        'detail_support_matches': [],
        'detail_negative_matches': [keyword for keyword, _ in detail_matches],
        'title_scored_matches': [{'keyword': keyword, 'deductStars': value} for keyword, value in title_matches],
        'detail_scored_matches': [{'keyword': keyword, 'deductStars': value} for keyword, value in detail_matches],
        'deductions': deductions,
        'reason': reason,
    }


def _load_resume() -> str:
    """自动选择简历：优先内联内容，其次按优先级扫描项目根目录下的简历文件。"""
    if Config.resume_content and Config.resume_content.strip():
        return Config.resume_content.strip()
    root = Path(__file__).resolve().parent
    # 优先使用精简/标准简历文件名，其余按文件名排序兜底。
    preferred = ['简历_精简.md', 'resume.md', '简历.md']
    seen: set[str] = set()
    candidates: list[Path] = []
    for name in preferred:
        path = root / name
        if path.exists() and path.is_file():
            candidates.append(path)
            seen.add(path.name)
    for path in sorted(root.iterdir()):
        if path.name in seen or not path.is_file() or path.suffix.lower() not in {'.md', '.txt'}:
            continue
        lower = path.name.lower()
        if 'resume' in lower or '简历' in path.name:
            candidates.append(path)
    for resume_path in candidates:
        content = resume_path.read_text(encoding='utf-8').strip()
        if content:
            return content
    return ''


async def generateCustomIntroduce(title: str, salary: str, detail: str, *, return_meta: bool = False) -> str | dict:
    """调用 LLM API 生成定制化打招呼语，失败时返回固定 introduce。"""
    def result(text: str, generated: bool, reason: str = '') -> str | dict:
        if return_meta:
            return {'introduce': text, 'generated': generated, 'fallbackReason': reason}
        return text

    if not LLM_MANAGER.available():
        return result(Config.introduce, False, '没有可用的大模型接口')

    resume = _load_resume()
    if not resume:
        print("[LLM] 未找到简历文件，使用固定 introduce", flush=True)
        return result(Config.introduce, False, '未找到简历')

    # 拼接岗位信息
    job_info = f"岗位名称：{title or '未知'}"
    if salary:
        job_info += f"\n薪资范围：{salary}"
    if detail:
        job_info += f"\n职位描述：\n{detail}"

    prompt = prompts.CUSTOM_INTRODUCE.format(
        resume=resume,
        job_info=job_info,
    )

    payload = {
        'messages': [
            {
                'role': 'system',
                'content': (
                    '你只负责输出可以直接发送给招聘者的最终招呼语。'
                    '禁止输出分析、思考过程、写作计划、标题、列表、草稿或解释。'
                ),
            },
            {'role': 'user', 'content': prompt},
        ],
        'temperature': 0.2,
        'max_tokens': 1024,
    }

    def extract_content(data: dict) -> tuple[str, str, str]:
        choice = data['choices'][0]
        msg = choice['message']
        content = _response_text(msg.get('content'))
        reasoning = _response_text(msg.get('reasoning_content')) or _response_text(msg.get('reasoning'))
        finish_reason = str(choice.get('finish_reason') or '')
        return content, reasoning, finish_reason

    def validate_content(content: str, finish_reason: str) -> tuple[str, str]:
        """只放行可直接发送的单段招呼语，绝不使用 reasoning 作为正文。"""
        if finish_reason.lower() == 'length':
            return '', '响应达到长度上限，内容可能被截断'
        text = (content or '').strip()
        if not text:
            return '', '响应没有最终正文'

        # 兼容模型偶尔返回的 JSON 或完整代码块，但不从混杂文本中猜测正文。
        fence_match = re.fullmatch(r'```(?:json|text)?\s*(.*?)\s*```', text, re.S | re.I)
        if fence_match:
            text = fence_match.group(1).strip()
        if text.startswith('{') and text.endswith('}'):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return '', '招呼语 JSON 无法解析'
            if not isinstance(parsed, dict) or set(parsed) != {'greeting'}:
                return '', '招呼语 JSON 结构不符合要求'
            text = str(parsed.get('greeting') or '').strip()

        if '\n' in text or '\r' in text:
            return '', '招呼语包含换行或多段内容'
        text = text.strip(' \t\r\n"\'“”‘’')
        if not text:
            return '', '清洗后没有最终正文'

        planning_pattern = re.compile(
            r'(?:草拟内容|草稿(?:内容)?|写作计划|思考过程|推理过程|'
            r'^(?:分析(?:过程|如下)?|思路|步骤|要求)\s*[：:]|'
            r'(?:以下是|下面是).{0,12}(?:分析|思路|步骤|草稿)|'
            r'(?:开头|介绍相关技能|介绍与岗位相关|选择突出|语气|收尾|字数|工作流程)\s*[：:]|'
            r'(?:首先|其次|然后|最后).{0,20}(?:提取|组织措辞|规划|生成(?:一段)?(?:回复|招呼)|回复))',
            re.I | re.M,
        )
        if planning_pattern.search(text):
            return '', '检测到分析、草稿或写作计划'
        if re.search(r'(?m)^\s*(?:[-*•]|\d+[.、)])\s+', text):
            return '', '检测到列表格式而不是最终招呼语'
        if '```' in text or re.search(r'(^|\s)#{1,6}\s+', text):
            return '', '检测到 Markdown 格式'

        text = re.sub(r'\s+', ' ', text).strip()
        compact_length = len(re.sub(r'\s+', '', text))
        if compact_length < 20:
            return '', f'招呼语过短（{compact_length}字）'
        if compact_length > 160:
            return '', f'招呼语过长（{compact_length}字）'
        return text, ''

    try:
        data = await LLM_MANAGER.chat_completions(payload, 'introduce')
        raw_content, reasoning, finish_reason = extract_content(data)
        content, invalid_reason = validate_content(raw_content, finish_reason)
        if not content:
            print(
                f'[LLM] 招呼语首次响应不可发送（{invalid_reason}; '
                f'finish_reason={finish_reason or "unknown"}; reasoning={"有" if reasoning else "无"}），正在纠正重试',
                flush=True,
            )
            retry_payload = copy.deepcopy(payload)
            retry_payload['max_tokens'] = max(int(payload['max_tokens']), 4096)
            retry_payload['messages'] = [
                {
                    'role': 'system',
                    'content': (
                        '上一条回复不能直接发送。请重新输出一条80至130字的中文招呼语。'
                        '只输出最终单段正文，禁止分析、推理、步骤、标题、列表、草稿、解释、引号和Markdown。'
                    ),
                },
                {
                    'role': 'user',
                    'content': f'{prompt}\n\n在内部完成信息筛选，只回复最终要发送给招聘者的一段完整文字。',
                },
            ]
            data = await LLM_MANAGER.chat_completions(retry_payload, 'introduce_retry')
            raw_content, reasoning, finish_reason = extract_content(data)
            content, invalid_reason = validate_content(raw_content, finish_reason)
        if not content:
            raise ValueError(
                f'LLM未返回可安全发送的最终招呼语（{invalid_reason}; '
                f'finish_reason={finish_reason or "unknown"}）'
            )
        print(f"[LLM] 定制 introduce 生成成功: {content[:50]}...", flush=True)
        return result(content, True)
    except Exception as e:
        _log_llm_fallback('定制招呼语生成', e, '使用固定招呼语')
        return result(Config.introduce, False, str(e))


async def llmJobFilter(title: str, salary: str, detail: str) -> tuple[bool, str]:
    """调用 LLM 判断岗位是否适合求职者，返回 (是否通过, 原因)。"""
    if not LLM_MANAGER.job_filter_enabled or not LLM_MANAGER.available():
        return True, '未启用AI筛选'

    resume = _load_resume()
    if not resume:
        return True, '无简历'

    job_info = f"岗位名称：{title or '未知'}"
    if salary:
        job_info += f"\n薪资范围：{salary}"
    if detail:
        job_info += f"\n职位描述：\n{detail}"

    prompt = prompts.JOB_FILTER.format(resume=resume, job_info=job_info)

    payload = {
        'messages': [
            {'role': 'system', 'content': '你是一个求职筛选助手。严格按格式输出：第一行true或false，第二行一句话原因。'},
            {'role': 'user', 'content': prompt},
        ],
        'temperature': 0.1,
        'max_tokens': 128,
    }

    try:
        data = await LLM_MANAGER.chat_completions(payload, 'job_filter')
        msg = data['choices'][0]['message']
        content = _response_text(msg.get('content'))
        reasoning = _response_text(msg.get('reasoning_content')) or _response_text(msg.get('reasoning'))
        if not content and reasoning:
            rc = reasoning
            rc_lines = [l.strip() for l in rc.split('\n') if l.strip()]
            content = '\n'.join(rc_lines[-3:]) if rc_lines else ''
        if not content:
            print(f"[LLM] 岗位筛选: {title} → AI返回空内容，默认通过", flush=True)
            return True, 'AI返回空内容'
        clean = re.sub(r'\*+', '', content)
        clean = re.sub(r'^#+\s*', '', clean, flags=re.MULTILINE)
        clean = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean)
        lines = [l.strip() for l in clean.split('\n') if l.strip()]
        first_line = lines[0].lower().strip()
        passed = first_line.startswith('true')
        reason = lines[1].strip() if len(lines) > 1 else ''
        if not reason:
            for keyword in ['因为', '原因', '：', ':', '。']:
                if keyword in lines[0]:
                    reason = lines[0].split(keyword, 1)[-1].strip()
                    break
        if not reason:
            reason = lines[0][:30]
        print(f"[LLM] 岗位筛选: {title} → {'通过' if passed else '不通过'} | 原因: {reason}", flush=True)
        return passed, reason
    except Exception as e:
        _log_llm_fallback('岗位筛选', e, '默认通过')
        return True, f'筛选异常: {str(e)[:200]}'


async def evaluateSingleRouteDelivery(job: str):
    match_result = evaluateJobMatch(job)
    return {
        **match_result,
        'introduce': Config.introduce,
        'resumeIndex': Config.frontend.get('resumeIndex', 0),
    }


def calcJobScore(job: str, resume: str):
    """计算职位匹配度"""
    return evaluateJobMatch(job)['score']
