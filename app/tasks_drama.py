"""Celery 任务：异步跑 pipeline + 写历史库。"""
import asyncio
from typing import Any, Optional

from app.celery_app import celery_app


@celery_app.task(bind=True, name="drama.generate_video")
def generate_drama_task(
    self,
    theme: str,
    style: str,
    duration: int,
    script: Optional[list[dict[str, Any]]] = None,
) -> dict:
    from app.crud import history as hist
    from app.services.pipeline_service import run_pipeline

    job_id = str(self.request.id)
    try:
        hist.mark_running(job_id)
        v, c, scenes_out = asyncio.run(
            run_pipeline(
                theme=theme,
                style=style,
                duration=duration,
                bgm_path=None,
                job_id=job_id,
                scenes=script,
            )
        )
        video_url = f"/static/{v.parent.name}/{v.name}"
        cover_url = f"/static/{c.parent.name}/{c.name}"
        hist.mark_completed(job_id, video_url, cover_url)
        return {
            "job_id": job_id,
            "video_url": video_url,
            "cover": cover_url,
            "script": scenes_out,
        }
    except Exception as e:
        hist.mark_failed(job_id, str(e))
        raise
