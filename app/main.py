"""FastAPI 入口：斩仙台短剧一键生成"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import drama


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.output_path.mkdir(parents=True, exist_ok=True)
    settings.temp_path.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="斩仙台短剧一键生成",
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
    return {
        "message": "斩仙台短剧一键生成 API",
        "docs": "/docs",
        "post": "POST /api/generate_short_drama",
        "body": {"theme": "斩仙台复仇", "style": "爽文", "duration": 60},
    }
