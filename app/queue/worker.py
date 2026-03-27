import asyncio
import uuid
from pathlib import Path
from typing import Any, Optional

from app.config import settings
from app.crud import history as hist
from app.queue.job_store import create_job, set_completed, set_failed, set_running
from app.services.pipeline_service import run_pipeline

_semaphore: Optional[asyncio.Semaphore] = None


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, getattr(settings, "queue_max_concurrent", 2)))
    return _semaphore


async def _run_job(
    job_id: str,
    theme: str,
    style: str,
    duration: int,
    bgm: Optional[Path] = None,
    scenes: Optional[list[dict[str, Any]]] = None,
    series_id: Optional[str] = None,
    episode: int = 1,
) -> None:
    async with _sem():
        hist.mark_running(job_id)
        set_running(job_id)
        try:
            v, c, script = await run_pipeline(
                theme=theme,
                style=style,
                duration=duration,
                bgm_path=bgm,
                job_id=job_id,
                scenes=scenes,
                series_id=series_id,
                episode=episode,
            )
            video_url = f"/static/{v.parent.name}/{v.name}"
            cover_url = f"/static/{c.parent.name}/{c.name}"
            hist.mark_completed(job_id, video_url, cover_url)
            set_completed(
                job_id,
                {"video_url": video_url, "cover": cover_url, "script": script},
            )
        except Exception as e:
            hist.mark_failed(job_id, str(e))
            set_failed(job_id, str(e))


def enqueue_async(
    theme: str,
    style: str,
    duration: int,
    scenes: Optional[list[dict[str, Any]]] = None,
    series_id: Optional[str] = None,
    episode: int = 1,
) -> str:
    job_id = uuid.uuid4().hex[:12]
    hist.create_pending(job_id, theme, style, duration)
    create_job(job_id, theme, style, duration)
    asyncio.create_task(
        _run_job(job_id, theme, style, duration, None, scenes, series_id, episode)
    )
    return job_id
