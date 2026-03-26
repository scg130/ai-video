"""FastAPI 入口：斩仙台短剧一键生成"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import settings
from app.db import init_db
from app.routers import drama

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.output_path.mkdir(parents=True, exist_ok=True)
    settings.temp_path.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(
    title="ai-video-generator",
    description="输入题材/风格/时长 → GPT 剧本 → TTS → 文生图 → FFmpeg 剪辑 → 成片",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(drama.router)

# 静态文件：/static/<job_id>/short_drama.mp4 等
drama.register_static(app, settings.output_path)


@app.get("/")
async def root():
    index = WEB_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {
        "message": "ai-video-generator API",
        "docs": "/docs",
        "ui": "添加 web/index.html 后访问 / 为控制台",
        "generate": "POST /api/generate",
        "status": "GET /api/status/{job_id}",
        "history": "GET /api/history",
    }
