"""一键流水线：两步剧本、变长分镜、TTS 多角色、图/视频、FFmpeg 增强、封面字。"""
import asyncio
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Optional

from app.config import settings
from app.services.media_fallback import placeholder_mp4
from app.services.script_service import (
    build_fallback_draft_scenes,
    generate_script,
    normalize_scenes_list,
)
from app.services.subtitle_service import to_srt
from app.services.tts_service import generate_tts_for_scenes
from app.services.image_service import generate_images_for_scenes
from app.services.visual_prompt import build_visual_prompt
from app.services.comfyui_cogvideox_service import generate_video_clip as cog_generate_clip
from app.services.comfyui_animatediff_service import generate_animatediff_clip as ad_generate_clip
from app.services.video_service import (
    build_video,
    build_video_from_clips,
    segment_durations_from_scenes,
    enhance_cover_with_title,
)


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
    jid = job_id or uuid.uuid4().hex[:12]
    temp_dir = settings.temp_path / jid
    temp_dir.mkdir(parents=True, exist_ok=True)
    out_dir = settings.output_path / jid
    out_dir.mkdir(parents=True, exist_ok=True)

    if scenes is not None:
        scenes = normalize_scenes_list(scenes)
    else:
        loop = asyncio.get_event_loop()
        try:
            scenes = await loop.run_in_executor(
                None,
                lambda: generate_script(
                    theme=theme,
                    style=style,
                    duration=duration,
                    series_id=series_id,
                    episode=max(1, int(episode)),
                ),
            )
        except Exception:
            if _pipeline_tolerant():
                scenes = build_fallback_draft_scenes(theme, style, duration, None)
            else:
                raise

    durs = segment_durations_from_scenes(scenes, default=5.0)

    srt_content = to_srt(scenes)
    srt_path = temp_dir / "subs.srt"
    srt_path.write_text(srt_content, encoding="utf-8")

    visual = (settings.visual_mode or "images").lower().strip()
    output_video = out_dir / "short_drama.mp4"
    cover_path = out_dir / "cover.png"
    cover_base = temp_dir / "cover_base.png"

    if visual == "cogvideox":
        neg = settings.sd_negative_prompt or settings.cogvideox_negative_default

        async def _cog_clips() -> list[Path]:
            out: list[Path] = []
            for i, s in enumerate(scenes):
                p = temp_dir / f"scene_{i:03d}.mp4"
                try:
                    await cog_generate_clip(build_visual_prompt(s), p, negative=neg)
                    if not p.exists() or p.stat().st_size < 64:
                        raise RuntimeError("CogVideoX 输出无效")
                except Exception:
                    if _pipeline_tolerant():
                        placeholder_mp4(p, durs[i] if i < len(durs) else 5.0)
                    else:
                        raise
                out.append(p)
            return out

        tts_paths, clip_paths = await asyncio.gather(
            generate_tts_for_scenes(scenes, temp_dir),
            _cog_clips(),
        )
        _cover_from_first_clip(clip_paths, cover_base)
        build_video_from_clips(
            clip_paths=clip_paths,
            audio_paths=tts_paths,
            scenes=scenes,
            srt_path=srt_path,
            output_video=output_video,
            bgm_path=bgm_path,
            seconds_per_clip=5.0,
            temp_dir=temp_dir,
            segment_durations=durs,
        )
    elif visual == "animatediff":
        neg = settings.sd_negative_prompt

        async def _ad_clips() -> list[Path]:
            out: list[Path] = []
            for i, s in enumerate(scenes):
                p = temp_dir / f"scene_{i:03d}.mp4"
                try:
                    await ad_generate_clip(build_visual_prompt(s), p, negative=neg)
                    if not p.exists() or p.stat().st_size < 64:
                        raise RuntimeError("AnimateDiff 输出无效")
                except Exception:
                    if _pipeline_tolerant():
                        placeholder_mp4(p, durs[i] if i < len(durs) else 5.0)
                    else:
                        raise
                out.append(p)
            return out

        tts_paths, clip_paths = await asyncio.gather(
            generate_tts_for_scenes(scenes, temp_dir),
            _ad_clips(),
        )
        _cover_from_first_clip(clip_paths, cover_base)
        build_video_from_clips(
            clip_paths=clip_paths,
            audio_paths=tts_paths,
            scenes=scenes,
            srt_path=srt_path,
            output_video=output_video,
            bgm_path=bgm_path,
            seconds_per_clip=5.0,
            temp_dir=temp_dir,
            segment_durations=durs,
        )
    else:
        tts_paths, image_paths = await asyncio.gather(
            generate_tts_for_scenes(scenes, temp_dir),
            generate_images_for_scenes(scenes, temp_dir),
        )
        if image_paths:
            shutil.copy2(image_paths[0], cover_base)
        build_video(
            image_paths=image_paths,
            audio_paths=tts_paths,
            scenes=scenes,
            srt_path=srt_path,
            output_video=output_video,
            bgm_path=bgm_path,
            seconds_per_image=5.0,
            temp_dir=temp_dir,
            segment_durations=durs,
        )

    if getattr(settings, "cover_promo_title", True) and cover_base.exists():
        try:
            enhance_cover_with_title(cover_base, _promo_title_from_scenes(scenes), cover_path, temp_dir)
        except Exception:
            shutil.copy2(cover_base, cover_path)
    elif cover_base.exists():
        shutil.copy2(cover_base, cover_path)

    return output_video, cover_path, scenes
