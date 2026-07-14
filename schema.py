"""大模型结构化输出使用的 Pydantic 数据模型。"""

from pydantic import BaseModel, Field
from typing import Annotated


class JobScore(BaseModel):
    """表示职位匹配度的整数分数。"""

    score: Annotated[int, Field(description='匹配度分数')]
