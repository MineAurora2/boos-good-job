"""简历读取及岗位相关的 LLM 任务。"""

from __future__ import annotations

import copy
import json
import re
import threading
import time

from app.config import Config
from app.llm import prompts
from app.llm.gateway import LLMGatewayError, response_text
from app.llm.manager import LLM_MANAGER
from app.storage.resume_store import load_resume_selection


_LLM_LOG_LOCK = threading.Lock()
_LLM_LAST_LOGGED_AT: dict[str, float] = {}


def _log_llm_fallback(operation: str, error: Exception, fallback: str) -> None:
    """限频记录同类 LLM 异常，避免接口故障时持续刷屏。"""
    status_code = error.status_code if isinstance(error, LLMGatewayError) else None
    category = (
        'circuit'
        if isinstance(error, LLMGatewayError) and error.circuit_open
        else str(status_code or type(error).__name__)
    )
    key = f'{operation}:{category}'
    now = time.monotonic()
    with _LLM_LOG_LOCK:
        last_logged = _LLM_LAST_LOGGED_AT.get(key, 0.0)
        if now - last_logged < 30:
            return
        _LLM_LAST_LOGGED_AT[key] = now
    summary = str(error).replace('\n', ' ')[:400]
    print(f'[LLM] {operation}失败，{fallback}: {summary}', flush=True)


def load_resume() -> str:
    """读取网页简历管理器选中的文件，作为所有 LLM 任务的简历来源。"""
    selected_name, content = load_resume_selection(Config.resume_name)
    return content.strip() if selected_name else ''


def _format_job_info(title: str, salary: str, detail: str) -> str:
    """构造两个岗位 LLM 任务共享的标准岗位信息。"""
    job_info = f"岗位名称：{title or '未知'}"
    if salary:
        job_info += f'\n薪资范围：{salary}'
    if detail:
        job_info += f'\n职位描述：\n{detail}'
    return job_info


async def generate_custom_introduce(
    title: str,
    salary: str,
    detail: str,
    *,
    return_meta: bool = False,
) -> str | dict:
    """生成可直接发送的定制招呼语，失败时返回配置中的固定招呼语。"""

    def result(text: str, generated: bool, reason: str = '') -> str | dict:
        if return_meta:
            return {'introduce': text, 'generated': generated, 'fallbackReason': reason}
        return text

    if not LLM_MANAGER.available():
        return result(Config.introduce, False, '没有可用的大模型接口')

    resume = load_resume()
    if not resume:
        print('[LLM] 未找到简历文件，使用固定 introduce', flush=True)
        return result(Config.introduce, False, '未找到简历')

    prompt = prompts.CUSTOM_INTRODUCE.format(
        resume=resume,
        job_info=_format_job_info(title, salary, detail),
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
        message = choice['message']
        content = response_text(message.get('content'))
        reasoning = response_text(message.get('reasoning_content')) or response_text(
            message.get('reasoning')
        )
        finish_reason = str(choice.get('finish_reason') or '')
        return content, reasoning, finish_reason

    def validate_content(content: str, finish_reason: str) -> tuple[str, str]:
        if finish_reason.lower() == 'length':
            return '', '响应达到长度上限，内容可能被截断'
        text = (content or '').strip()
        if not text:
            return '', '响应没有最终正文'

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
        print(f'[LLM] 定制 introduce 生成成功: {content[:50]}...', flush=True)
        return result(content, True)
    except Exception as error:
        _log_llm_fallback('定制招呼语生成', error, '使用固定招呼语')
        return result(Config.introduce, False, str(error))


async def llm_job_filter(title: str, salary: str, detail: str) -> tuple[bool, str]:
    """调用 LLM 判断岗位是否适合求职者，返回是否通过及原因。"""
    if not LLM_MANAGER.job_filter_enabled or not LLM_MANAGER.available():
        return True, '未启用AI筛选'

    resume = load_resume()
    if not resume:
        return True, '无简历'

    prompt = prompts.JOB_FILTER.format(
        resume=resume,
        job_info=_format_job_info(title, salary, detail),
    )
    payload = {
        'messages': [
            {
                'role': 'system',
                'content': '你是一个求职筛选助手。严格按格式输出：第一行true或false，第二行一句话原因。',
            },
            {'role': 'user', 'content': prompt},
        ],
        'temperature': 0.1,
        'max_tokens': 128,
    }

    try:
        data = await LLM_MANAGER.chat_completions(payload, 'job_filter')
        message = data['choices'][0]['message']
        content = response_text(message.get('content'))
        reasoning = response_text(message.get('reasoning_content')) or response_text(
            message.get('reasoning')
        )
        if not content and reasoning:
            reasoning_lines = [line.strip() for line in reasoning.split('\n') if line.strip()]
            content = '\n'.join(reasoning_lines[-3:]) if reasoning_lines else ''
        if not content:
            print(f'[LLM] 岗位筛选: {title} → AI返回空内容，默认通过', flush=True)
            return True, 'AI返回空内容'
        clean = re.sub(r'\*+', '', content)
        clean = re.sub(r'^#+\s*', '', clean, flags=re.MULTILINE)
        clean = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean)
        lines = [line.strip() for line in clean.split('\n') if line.strip()]
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
        print(
            f'[LLM] 岗位筛选: {title} → {"通过" if passed else "不通过"} | 原因: {reason}',
            flush=True,
        )
        return passed, reason
    except Exception as error:
        _log_llm_fallback('岗位筛选', error, '默认通过')
        return True, f'筛选异常: {str(error)[:200]}'


__all__ = ['generate_custom_introduce', 'llm_job_filter', 'load_resume']
