"""请求/响应模型"""
from typing import Any, Optional

from pydantic import BaseModel, Field


class SceneItem(BaseModel):
    """单镜分镜（与 GPT 输出字段对齐）"""
    time: str = Field(description="如 0-5s、0-3s")
    scene: str = Field(description="画面描述")
    camera: str = Field(default="", description="镜头语言")
    dialogue: str = Field(description="人物台词")
    emotion: str = Field(description="情绪")
    role: str = Field(default="主角", description="主角/反派/女主/路人/旁白")


class GenerateShortDramaRequest(BaseModel):
    theme: str = Field(default="斩仙台复仇", description="题材关键词")
    style: str = Field(default="爽文", description="风格：爽文、反转、打脸等")
    duration: int = Field(default=60, ge=30, le=120, description="时长秒数")
    series_id: str = Field(default="", description="连续剧 ID，与 episode 配合走 Chroma 记忆")
    episode: int = Field(default=1, ge=1, description="第几集（第 1 集也会写入记忆）")


class GenerateShortDramaResponse(BaseModel):
    video_url: str = Field(description="成片访问路径/URL")
    cover: str = Field(description="封面图路径/URL")
    script: list[dict[str, Any]] = Field(description="分镜脚本 JSON")


class JobEnqueueResponse(BaseModel):
    job_id: str = Field(description="任务 ID（Celery 为 task id，内存队列为短 id）")


class JobStatusResponse(BaseModel):
    job_id: str
    status: str = Field(description="pending | running | completed | failed | PENDING | STARTED | SUCCESS | FAILURE")
    video_url: Optional[str] = None
    cover: Optional[str] = None
    script: Optional[list[dict[str, Any]]] = None
    error: Optional[str] = None


class GenerateApiResponse(BaseModel):
    """POST /api/generate 返回（前端轮询用）"""
    job_id: str


class PublicStatusResponse(BaseModel):
    """GET /api/status/{job_id}：status 使用 done 表示完成（便于前端判断）"""
    job_id: str
    status: str = Field(description="pending | running | done | failed")
    video_url: Optional[str] = None
    cover: Optional[str] = None
    error: Optional[str] = None


class HistoryVideoItem(BaseModel):
    job_id: str
    theme: str
    style: str
    duration: int
    video_url: Optional[str] = None
    cover_url: Optional[str] = None
    status: str
    created_at: str


class DraftScriptRequest(BaseModel):
    """根据简介让大模型生成分镜剧本（第一步）。"""
    theme: str = Field(..., description="主题")
    style: str = Field(default="爽文", description="风格")
    synopsis: str = Field(default="", description="故事简介，越具体越好")
    duration: int = Field(default=60, ge=30, le=120, description="目标总时长（秒）")
    series_id: str = Field(default="", description="连续剧 ID（可选），用于检索历史与写回记忆")
    episode: int = Field(default=1, ge=1, description="第几集")


class DraftScriptResponse(BaseModel):
    script: list[dict[str, Any]] = Field(description="分镜 JSON 数组，可给用户编辑后再提交生成视频")
    ok: bool = Field(default=True, description="true 表示大模型正常生成；false 为兜底模板")
    fallback: bool = Field(default=False, description="true 表示 script 为服务端占位模板，需用户自行改写")
    error_code: Optional[str] = Field(default=None, description="失败时原因码，如 openai_unavailable / draft_failed")
    message: Optional[str] = Field(default=None, description="失败简述；成功时一般为 null")


class RagMaterialIngestRequest(BaseModel):
    """写入 Chroma「资料」库，供剧本生成时语义检索。"""
    text: str = Field(..., description="资料正文（设定、百科、梗概等）")
    doc_id: str = Field(default="", description="可选固定 ID；留空则自动生成")
    tags: str = Field(default="", description="可选标签，便于后续筛选（当前仅存元数据）")


class RagMaterialIngestResponse(BaseModel):
    ok: bool = Field(description="是否写入成功")
    doc_id: str = Field(default="", description="实际使用的文档 ID")


class OneLinerExpandRequest(BaseModel):
    """一句话扩写：剧本 + 分镜 + 每镜文生图 prompt + 配音文案。"""
    line: str = Field(..., description="一句话创意或梗概")
    style: str = Field(default="爽文", description="风格")
    duration: int = Field(default=60, ge=30, le=120, description="目标总时长（秒）")


class OneLinerExpandResponse(BaseModel):
    script: str = Field(description="剧本梗概（扩写后的正文）")
    scenes: list[dict[str, Any]] = Field(
        description="分镜列表；每项含 time, scene, camera, dialogue, emotion, role, image_prompt, voice_text"
    )
    ok: bool = Field(default=True, description="大模型是否成功；false 时为兜底")
    fallback: bool = Field(default=False, description="true 表示兜底占位，需用户改写")
    error_code: Optional[str] = Field(default=None, description="失败原因码")
    message: Optional[str] = Field(default=None, description="失败简述")


class GenerateVideoRequest(BaseModel):
    """用户确认/编辑后的剧本 + 元数据，异步生成成片（第二步）。"""
    theme: str = Field(..., description="主题（入历史库）")
    style: str = Field(default="爽文")
    duration: int = Field(default=60, ge=30, le=120)
    script: list[dict[str, Any]] = Field(..., description="分镜列表，每项含 time, scene, camera, dialogue, emotion, role")
