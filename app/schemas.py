"""请求/响应模型"""
from typing import Any

from pydantic import BaseModel, Field


class SceneItem(BaseModel):
    """单镜分镜"""
    time: str = Field(description="如 0-5s")
    scene: str = Field(description="画面描述")
    dialogue: str = Field(description="人物台词")
    emotion: str = Field(description="情绪")


class GenerateShortDramaRequest(BaseModel):
    theme: str = Field(default="斩仙台复仇", description="题材关键词")
    style: str = Field(default="爽文", description="风格：爽文、反转、打脸等")
    duration: int = Field(default=60, ge=30, le=120, description="时长秒数")


class GenerateShortDramaResponse(BaseModel):
    video_url: str = Field(description="成片访问路径/URL")
    cover: str = Field(description="封面图路径/URL")
    script: list[dict[str, Any]] = Field(description="分镜脚本 JSON")
