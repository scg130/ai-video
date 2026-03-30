"""一键流水线：LangGraph 编排见 app.graph.pipeline_graph；此处保留子图共用的辅助函数。"""
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from app.config import settings


def _cover_from_first_clip(clip_paths: list[Path], cover_path: Path) -> None:
    if not clip_paths:
        return
    first = clip_paths[0]
    if first.suffix.lower() in (".mp4", ".webm", ".mov", ".avi"):
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(first), "-vframes", "1", str(cover_path)],
            check=True,
            capture_output=True,
        )
    else:
        shutil.copy2(first, cover_path)


def _pipeline_tolerant() -> bool:
    return getattr(settings, "pipeline_fault_tolerant", True)


def _promo_title_from_scenes(scenes: list[dict]) -> str:
    if not scenes:
        return ""
    d = (scenes[0].get("dialogue") or "").strip()
    if d:
        return d[:24]
    return (scenes[0].get("scene") or "")[:20]


async def run_pipeline(
    theme: str,
    style: str,
    duration: int,
    bgm_path: Optional[Path] = None,
    job_id: Optional[str] = None,
    scenes: Optional[list[dict[str, Any]]] = None,
    series_id: Optional[str] = None,
    episode: int = 1,
) -> tuple[Path, Path, list[dict]]:
    from app.graph.pipeline_graph import invoke_drama_pipeline

    return await invoke_drama_pipeline(
        theme=theme,
        style=style,
        duration=duration,
        bgm_path=bgm_path,
        job_id=job_id,
        scenes=scenes,
        series_id=series_id,
        episode=episode,
    )
