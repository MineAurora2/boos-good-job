"""处理大模型纯文本响应的轻量解析工具。"""

import re


def getLLMReply(content: str) -> str:
    """移除部分推理模型返回的 ``<think>`` 前缀，只保留最终回复。"""
    return content.split('</think>\n')[-1].strip()


def getMatchScore(text: str) -> int | None:
    """从纯数字或包含“匹配”字样的文本中提取匹配分数。"""
    # 如果只有数值
    if re.search(r'^\d+$', text):
        return int(text)
    # 分成多行，寻找匹配度
    for i in text.split('\n'):
        if re.search(r'匹配.*?\d+', i):
            return int(re.search(r'\d+', i).group())
    return None
