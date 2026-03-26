"""Celery 应用（USE_CELERY=true 时使用）。"""
from celery import Celery

from app.config import settings

celery_app = Celery(
    "ai_video",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    task_track_started=True,
)

# 注册任务模块（worker 启动时需能 import）
import importlib

importlib.import_module("app.tasks_drama")
