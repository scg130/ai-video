"""SQLite 表：视频生成历史。"""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Video(SQLModel, table=True):
    __tablename__ = "videos"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(index=True, unique=True, max_length=128)
    theme: str = Field(max_length=512)
    style: str = Field(default="", max_length=64)
    duration: int = Field(default=60)
    video_url: Optional[str] = Field(default=None, max_length=1024)
    cover_url: Optional[str] = Field(default=None, max_length=1024)
    status: str = Field(default="pending", max_length=32)  # pending | running | completed | failed
    error: Optional[str] = Field(default=None, max_length=4096)
    created_at: datetime = Field(default_factory=datetime.utcnow)
