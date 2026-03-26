"""流水线容错：占位图 / 占位视频片段（FFmpeg lavfi，无额外依赖）。"""
import subprocess
from pathlib import Path

from app.config import settings


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y"] + args, check=True, capture_output=True)


def placeholder_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    w = getattr(settings, "video_output_width", 720)
    h = getattr(settings, "video_output_height", 1280)
    _run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x1a1528:s={w}x{h}",
            "-frames:v",
            "1",
            str(path),
        ]
    )


def placeholder_mp4(path: Path, duration: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    w = getattr(settings, "video_output_width", 720)
    h = getattr(settings, "video_output_height", 1280)
    fps = getattr(settings, "ffmpeg_fps", 24)
    d = max(0.5, float(duration))
    _run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={w}x{h}:r={fps}",
            "-t",
            str(d),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(path),
        ]
    )
