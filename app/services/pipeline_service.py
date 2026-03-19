"""一键流水线：剧本 → TTS → 文生图/文生视频 → 自动剪辑 → 成片"""
import asyncio
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from app.config import settings
from app.services.script_service import generate_script
from app.services.subtitle_service import to_srt
from app.services.tts_service import generate_tts_for_scenes
from app.services.image_service import generate_images_for_scenes
from app.services.video_service import build_video, build_video_from_clips
from app.services.comfyui_cogvideox_service import generate_cogvideox_clips_for_scenes


async def run_pipeline(
    theme: str,
    style: str,
    duration: int,
    bgm_path: Optional[Path] = None,
) -> tuple[Path, Path, list[dict]]:
    """
    执行完整流水线，返回 (成片路径, 封面路径, 脚本)。
    """
    job_id = uuid.uuid4().hex[:12]
    temp_dir = settings.temp_path / job_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    out_dir = settings.output_path / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) 剧本（同步 GPT 调用放线程池，避免阻塞）
    loop = asyncio.get_event_loop()
    scenes = await loop.run_in_executor(
        None, lambda: generate_script(theme=theme, style=style, duration=duration)
    )

    # 2) SRT
    srt_content = to_srt(scenes)
    srt_path = temp_dir / "subs.srt"
    srt_path.write_text(srt_content, encoding="utf-8")

    # 3) TTS + 4) 画面：文生图 或 ComfyUI CogVideoX 文生视频
    visual = (settings.visual_mode or "images").lower().strip()
    if visual == "cogvideox":
        tts_paths, clip_paths = await asyncio.gather(
            generate_tts_for_scenes(scenes, temp_dir),
            generate_cogvideox_clips_for_scenes(scenes, temp_dir),
        )
        output_video = out_dir / "short_drama.mp4"
        cover_path = out_dir / "cover.png"
        if clip_paths:
            first = clip_paths[0]
            if first.suffix.lower() in (".mp4", ".webm", ".mov", ".avi"):
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", str(first),
                        "-vframes", "1", str(cover_path),
                    ],
                    check=True,
                    capture_output=True,
                )
            else:
                shutil.copy2(first, cover_path)
        build_video_from_clips(
            clip_paths=clip_paths,
            audio_paths=tts_paths,
            scenes=scenes,
            srt_path=srt_path,
            output_video=output_video,
            bgm_path=bgm_path,
            seconds_per_clip=5.0,
            temp_dir=temp_dir,
        )
    else:
        tts_paths, image_paths = await asyncio.gather(
            generate_tts_for_scenes(scenes, temp_dir),
            generate_images_for_scenes(scenes, temp_dir),
        )
        output_video = out_dir / "short_drama.mp4"
        cover_path = out_dir / "cover.png"
        if image_paths:
            shutil.copy2(image_paths[0], cover_path)
        build_video(
            image_paths=image_paths,
            audio_paths=tts_paths,
            scenes=scenes,
            srt_path=srt_path,
            output_video=output_video,
            bgm_path=bgm_path,
            seconds_per_image=5.0,
            temp_dir=temp_dir,
        )

    return output_video, cover_path, scenes
