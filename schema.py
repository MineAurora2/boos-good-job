from pydantic import BaseModel, Field
from typing import Annotated


class JobScore(BaseModel):
    score: Annotated[int, Field(description='匹配度分数')]
