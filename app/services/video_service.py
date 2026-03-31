"""自动剪辑：Ken Burns、xfade 转场、抖音风字幕、封面大字。"""
import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from app.config import settings

_log = logging.getLogger(__name__)


def _compose_log(msg: str, *args: object) -> None:
    _log.info("[合成视频] " + msg, *args)


def _run_ffmpeg(args: list[str], cwd: Optional[Path] = None) -> None:
    subprocess.run(
        ["ffmpeg", "-y"] + args,
        check=True,
        capture_output=True,
        cwd=cwd,
    )


def segment_durations_from_scenes(scenes: list[dict], default: float = 5.0) -> list[float]:
    """从分镜 time 字段解析每段时长（秒），如 0-3s → 3，3-8s → 5。"""
    out: list[float] = []
    for i, s in enumerate(scenes):
        t = str(s.get("time", f"{i * int(default)}-{(i + 1) * int(default)}s"))
        try:
            t = t.lower().replace("s", "").strip()
            parts = t.split("-", 1)
            a, b = float(parts[0].strip()), float(parts[1].strip())
            out.append(max(1.0, b - a))
        except (ValueError, IndexError):
            out.append(default)
    return out


def _subtitle_filter_arg(srt_path: Path) -> str:
    srt_esc = str(srt_path.absolute()).replace("\\", "/").replace(":", "\\:")
    style = getattr(settings, "ffmpeg_subtitle_style", "Fontsize=28,Bold=1,PrimaryColour=&H00FFFFFF&,Outline=3")
    return f"subtitles='{srt_esc}':force_style='{style}'"


def _image_to_segment_mp4(
    img_path: Path,
    out_path: Path,
    duration: float,
    temp_dir: Path,
    use_ken_burns: bool,
) -> None:
    w = settings.video_output_width
    h = settings.video_output_height
    fps = settings.ffmpeg_fps
    d_frames = max(1, int(duration * fps))
    temp_dir.mkdir(parents=True, exist_ok=True)
    if use_ken_burns:
        vf = (
            f"scale=iw*3:ih*3,zoompan=z='min(zoom+0.0012,1.35)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={d_frames}:s={w}x{h}:fps={fps}"
        )
    else:
        vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps}"
    _run_ffmpeg([
        "-loop", "1", "-i", str(img_path),
        "-vf", vf,
        "-t", str(duration),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ])


def _xfade_concat_segments(
    segment_paths: List[Path],
    durations: List[float],
    out_path: Path,
    fade_d: float,
) -> None:
    n = len(segment_paths)
    if n == 0:
        raise ValueError("无视频片段")
    if n == 1:
        shutil.copy2(segment_paths[0], out_path)
        return
    inputs = []
    for p in segment_paths:
        inputs.extend(["-i", str(p)])
    parts: list[str] = []
    acc = "[0:v]"
    for i in range(1, n):
        offset = sum(durations[:i]) - i * fade_d
        outlab = f"xv{i}" if i < n - 1 else "vout"
        parts.append(f"{acc}[{i}:v]xfade=transition=fade:duration={fade_d}:offset={offset}[{outlab}]")
        acc = f"[{outlab}]"
    fc = ";".join(parts)
    _run_ffmpeg(inputs + ["-filter_complex", fc, "-map", "[vout]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path)])


def _concat_demuxer_segments(segment_paths: List[Path], out_path: Path, temp_dir: Path) -> None:
    lst = temp_dir / "seg_concat.txt"
    with open(lst, "w") as f:
        for p in segment_paths:
            f.write(f"file '{p.absolute()}'\n")
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out_path)])


def build_video(
    image_paths: List[Path],
    audio_paths: List[Optional[Path]],
    scenes: list[dict],
    srt_path: Path,
    output_video: Path,
    bgm_path: Optional[Path] = None,
    seconds_per_image: float = 5.0,
    temp_dir: Optional[Path] = None,
    segment_durations: Optional[List[float]] = None,
) -> Path:
    """
    图序列 →（可选 Ken Burns）→（可选 xfade）→ 配音 → 字幕 → BGM。
    """
    temp_dir = temp_dir or output_video.parent
    temp_dir.mkdir(parents=True, exist_ok=True)
    durs = segment_durations or [seconds_per_image] * len(image_paths)
    if len(durs) < len(image_paths):
        durs.extend([seconds_per_image] * (len(image_paths) - len(durs)))

    use_kb = getattr(settings, "ffmpeg_ken_burns", True)
    use_xf = getattr(settings, "ffmpeg_xfade", True) and len(image_paths) > 1
    fade_d = float(getattr(settings, "ffmpeg_xfade_duration", 0.5))
    total_d = sum(durs)
    _compose_log(
        "开始(静图模式) segments=%d 总时长约%.1fs → %s temp=%s ken_burns=%s xfade=%s",
        len(image_paths),
        total_d,
        output_video,
        temp_dir,
        use_kb,
        use_xf,
    )

    segs: List[Path] = []
    for i, p in enumerate(image_paths):
        seg = temp_dir / f"kb_{i:03d}.mp4"
        _compose_log("静图→片段 [%d/%d] %s %.1fs", i + 1, len(image_paths), p.name, durs[i])
        _image_to_segment_mp4(p, seg, durs[i], temp_dir, use_kb)
        segs.append(seg)

    video_no_audio = temp_dir / "video_no_audio.mp4"
    if use_xf:
        _compose_log("视频轨拼接 xfade fade=%.2fs → %s", fade_d, video_no_audio.name)
        _xfade_concat_segments(segs, durs, video_no_audio, fade_d)
    else:
        _compose_log("视频轨拼接 concat demuxer → %s", video_no_audio.name)
        _concat_demuxer_segments(segs, video_no_audio, temp_dir)

    voice_concat_list = temp_dir / "voice_list.txt"
    with open(voice_concat_list, "w") as f:
        for i, ap in enumerate(audio_paths):
            dur = durs[i] if i < len(durs) else seconds_per_image
            if ap and ap.exists():
                f.write(f"file '{ap.absolute()}'\n")
            else:
                silent = temp_dir / f"silent_{i}.mp3"
                _run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(dur), "-q:a", "9", str(silent)])
                f.write(f"file '{silent.absolute()}'\n")
    voice_merged = temp_dir / "voice_merged.mp3"
    _compose_log("配音轨 concat → %s", voice_merged.name)
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(voice_concat_list), "-c", "copy", str(voice_merged)])

    video_with_voice = temp_dir / "video_with_voice.mp4"
    _compose_log("合并 画面+配音 → %s", video_with_voice.name)
    _run_ffmpeg([
        "-i", str(video_no_audio),
        "-i", str(voice_merged),
        "-c:v", "copy", "-c:a", "aac", "-shortest",
        str(video_with_voice),
    ])

    video_with_subs = temp_dir / "video_with_subs.mp4"
    _compose_log("烧录字幕 srt=%s → %s", srt_path.name, video_with_subs.name)
    _run_ffmpeg([
        "-i", str(video_with_voice),
        "-vf", _subtitle_filter_arg(srt_path),
        "-c:a", "copy",
        str(video_with_subs),
    ])

    if bgm_path and bgm_path.exists():
        _compose_log("混音 BGM %s → %s", bgm_path.name, output_video.name)
        _run_ffmpeg([
            "-i", str(video_with_subs),
            "-i", str(bgm_path),
            "-filter_complex", "[1:a]volume=0.2[a1];[0:a][a1]amix=inputs=2:duration=shortest[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-shortest",
            str(output_video),
        ])
    else:
        _compose_log("无 BGM，复制为成片 → %s", output_video.name)
        shutil.copy2(video_with_subs, output_video)

    if output_video.exists():
        _compose_log("完成(静图) %s (%d bytes)", output_video, output_video.stat().st_size)
    return output_video


def _normalize_clip_to_duration(
    clip_path: Path,
    seconds: float,
    temp_dir: Path,
    index: int,
) -> Path:
    temp_dir.mkdir(parents=True, exist_ok=True)
    out = temp_dir / f"norm_{index:03d}.mp4"
    suf = clip_path.suffix.lower()
    if suf in (".png", ".jpg", ".jpeg", ".webp"):
        _run_ffmpeg([
            "-loop", "1", "-i", str(clip_path),
            "-c:v", "libx264", "-t", str(seconds), "-pix_fmt", "yuv420p",
            "-r", str(settings.ffmpeg_fps), "-movflags", "+faststart",
            str(out),
        ])
        return out
    _run_ffmpeg([
        "-i", str(clip_path),
        "-t", str(seconds),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(settings.ffmpeg_fps),
        "-an", "-movflags", "+faststart",
        str(out),
    ])
    return out


def build_video_from_clips(
    clip_paths: List[Path],
    audio_paths: List[Optional[Path]],
    scenes: list[dict],
    srt_path: Path,
    output_video: Path,
    bgm_path: Optional[Path] = None,
    seconds_per_clip: float = 5.0,
    temp_dir: Optional[Path] = None,
    segment_durations: Optional[List[float]] = None,
) -> Path:
    temp_dir = temp_dir or output_video.parent
    temp_dir.mkdir(parents=True, exist_ok=True)
    durs = segment_durations or [seconds_per_clip] * len(clip_paths)
    if len(durs) < len(clip_paths):
        durs.extend([seconds_per_clip] * (len(clip_paths) - len(durs)))
    total_d = sum(durs)
    use_xf = getattr(settings, "ffmpeg_xfade", True) and len(clip_paths) > 1
    fade_d = float(getattr(settings, "ffmpeg_xfade_duration", 0.5))
    _compose_log(
        "开始(视频片段模式) clips=%d 总时长约%.1fs → %s temp=%s xfade=%s",
        len(clip_paths),
        total_d,
        output_video,
        temp_dir,
        use_xf,
    )

    normalized: List[Path] = []
    for i, p in enumerate(clip_paths):
        dur = durs[i] if i < len(durs) else seconds_per_clip
        _compose_log("片段归一化 [%d/%d] %s %.1fs", i + 1, len(clip_paths), p.name, dur)
        normalized.append(_normalize_clip_to_duration(p, dur, temp_dir, i))

    video_no_audio = temp_dir / "clips_concat.mp4"
    if use_xf:
        _compose_log("视频轨拼接 xfade fade=%.2fs → %s", fade_d, video_no_audio.name)
        _xfade_concat_segments(normalized, durs, video_no_audio, fade_d)
    else:
        _compose_log("视频轨拼接 concat demuxer → %s", video_no_audio.name)
        _concat_demuxer_segments(normalized, video_no_audio, temp_dir)

    voice_concat_list = temp_dir / "voice_list.txt"
    with open(voice_concat_list, "w") as f:
        for i, ap in enumerate(audio_paths):
            dur = durs[i] if i < len(durs) else seconds_per_clip
            if ap and ap.exists():
                f.write(f"file '{ap.absolute()}'\n")
            else:
                silent = temp_dir / f"silent_{i}.mp3"
                _run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(dur), "-q:a", "9", str(silent)])
                f.write(f"file '{silent.absolute()}'\n")
    voice_merged = temp_dir / "voice_merged.mp3"
    _compose_log("配音轨 concat → %s", voice_merged.name)
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(voice_concat_list), "-c", "copy", str(voice_merged)])

    video_with_voice = temp_dir / "video_with_voice.mp4"
    _compose_log("合并 画面+配音 → %s", video_with_voice.name)
    _run_ffmpeg([
        "-i", str(video_no_audio),
        "-i", str(voice_merged),
        "-c:v", "copy", "-c:a", "aac", "-shortest",
        str(video_with_voice),
    ])

    video_with_subs = temp_dir / "video_with_subs.mp4"
    _compose_log("烧录字幕 srt=%s → %s", srt_path.name, video_with_subs.name)
    _run_ffmpeg([
        "-i", str(video_with_voice),
        "-vf", _subtitle_filter_arg(srt_path),
        "-c:a", "copy",
        str(video_with_subs),
    ])

    if bgm_path and bgm_path.exists():
        _compose_log("混音 BGM %s → %s", bgm_path.name, output_video.name)
        _run_ffmpeg([
            "-i", str(video_with_subs),
            "-i", str(bgm_path),
            "-filter_complex", "[1:a]volume=0.2[a1];[0:a][a1]amix=inputs=2:duration=shortest[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-shortest",
            str(output_video),
        ])
    else:
        _compose_log("无 BGM，复制为成片 → %s", output_video.name)
        shutil.copy2(video_with_subs, output_video)

    if output_video.exists():
        _compose_log("完成(片段) %s (%d bytes)", output_video, output_video.stat().st_size)
    return output_video


def enhance_cover_with_title(cover_src: Path, title: str, out_path: Path, temp_dir: Path) -> None:
    """封面叠加大字标题（金/红抖音风），title 写入临时文件避免 shell 转义问题。"""
    if not title or not title.strip():
        shutil.copy2(cover_src, out_path)
        return
    temp_dir.mkdir(parents=True, exist_ok=True)
    tf = temp_dir / "cover_title.txt"
    tf.write_text(title.strip()[:40], encoding="utf-8")
    tfp = str(tf.absolute()).replace("\\", "/").replace(":", "\\:")
    vf = (
        f"drawtext=textfile='{tfp}':fontcolor=#FFD700:fontsize=56:"
        f"x=(w-text_w)/2:y=h*0.08:borderw=4:bordercolor=black@0.8:box=1:boxcolor=black@0.35"
    )
    _run_ffmpeg(["-i", str(cover_src), "-vf", vf, "-y", str(out_path)])
