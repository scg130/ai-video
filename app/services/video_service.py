"""自动剪辑：FFmpeg 拼接图片+配音+字幕+BGM，导出短视频。"""
import subprocess
import shutil
from pathlib import Path
from typing import List, Optional


def _run_ffmpeg(args: list[str], cwd: Optional[Path] = None) -> None:
    subprocess.run(
        ["ffmpeg", "-y"] + args,
        check=True,
        capture_output=True,
        cwd=cwd,
    )


def build_video(
    image_paths: List[Path],
    audio_paths: List[Optional[Path]],
    scenes: list[dict],
    srt_path: Path,
    output_video: Path,
    bgm_path: Optional[Path] = None,
    seconds_per_image: float = 5.0,
    temp_dir: Optional[Path] = None,
) -> Path:
    """
    用 FFmpeg 做：
    1. 图片序列 + 每张时长 -> 无音视频
    2. 按句拼接 TTS 音频（或静音）成一条音轨
    3. 混流：视频 + 配音
    4. 烧录字幕
    5. 可选混 BGM
    返回最终视频路径。
    """
    temp_dir = temp_dir or output_video.parent
    temp_dir.mkdir(parents=True, exist_ok=True)
    # 1) 图片序列 -> 视频（固定帧率，每张 5 秒）
    # ffmpeg -r 1/5 -i img_%03d.png -c:v libx264 -pix_fmt yuv420p -r 24 video_no_audio.mp4
    concat_list = temp_dir / "concat_list.txt"
    with open(concat_list, "w") as f:
        for p in image_paths:
            f.write(f"file '{p.absolute()}'\nduration {seconds_per_image}\n")
        # 最后一张要再写一次（无 duration）否则 ffmpeg 会丢
        if image_paths:
            f.write(f"file '{image_paths[-1].absolute()}'\n")

    video_no_audio = temp_dir / "video_no_audio.mp4"
    _run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24",
        "-movflags", "+faststart",
        str(video_no_audio),
    ])

    # 2) 拼接所有 TTS 片段为一条配音（与分镜一一对应，无台词处用静音）
    voice_concat_list = temp_dir / "voice_list.txt"
    with open(voice_concat_list, "w") as f:
        for i, ap in enumerate(audio_paths):
            if ap and ap.exists():
                f.write(f"file '{ap.absolute()}'\n")
            else:
                # 静音 5 秒
                silent = temp_dir / f"silent_{i}.mp3"
                _run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(seconds_per_image), "-q:a", "9", str(silent)])
                f.write(f"file '{silent.absolute()}'\n")
    voice_merged = temp_dir / "voice_merged.mp3"
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(voice_concat_list), "-c", "copy", str(voice_merged)])

    # 3) 视频 + 配音（-shortest 以视频为准）
    video_with_voice = temp_dir / "video_with_voice.mp4"
    _run_ffmpeg([
        "-i", str(video_no_audio),
        "-i", str(voice_merged),
        "-c:v", "copy", "-c:a", "aac", "-shortest",
        str(video_with_voice),
    ])

    # 4) 烧录字幕（SRT 路径需转义给 filter）
    # 使用 subtitles filter: 注意 Windows 路径要转义，这里用 Path 且假设无特殊字符
    srt_esc = str(srt_path.absolute()).replace("\\", "/").replace(":", "\\:")
    video_with_subs = temp_dir / "video_with_subs.mp4"
    _run_ffmpeg([
        "-i", str(video_with_voice),
        "-vf", f"subtitles='{srt_esc}':force_style='Fontsize=24,PrimaryColour=&Hffffff&,Outline=2'",
        "-c:a", "copy",
        str(video_with_subs),
    ])

    # 5) 可选 BGM 混音（配音主，BGM 压低）
    if bgm_path and bgm_path.exists():
        _run_ffmpeg([
            "-i", str(video_with_subs),
            "-i", str(bgm_path),
            "-filter_complex", "[1:a]volume=0.2[a1];[0:a][a1]amix=inputs=2:duration=shortest[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-shortest",
            str(output_video),
        ])
    else:
        shutil.copy2(video_with_subs, output_video)

    return output_video


def _normalize_clip_to_duration(
    clip_path: Path,
    seconds: float,
    temp_dir: Path,
    index: int,
) -> Path:
    """
    将单段素材统一为固定时长视频（mp4）：视频则裁剪/循环到 seconds；单张图则拉成静态视频。
    """
    temp_dir.mkdir(parents=True, exist_ok=True)
    out = temp_dir / f"norm_{index:03d}.mp4"
    suf = clip_path.suffix.lower()
    if suf in (".png", ".jpg", ".jpeg", ".webp"):
        _run_ffmpeg([
            "-loop", "1", "-i", str(clip_path),
            "-c:v", "libx264", "-t", str(seconds), "-pix_fmt", "yuv420p",
            "-r", "24", "-movflags", "+faststart",
            str(out),
        ])
        return out
    # 视频：先裁剪到最多 seconds（短则保持原长，由 concat 后 -shortest 与音轨对齐）
    _run_ffmpeg([
        "-i", str(clip_path),
        "-t", str(seconds),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24",
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
) -> Path:
    """
    由多段 ComfyUI/CogVideoX 生成的视频（或回退图片）拼接，再混 TTS、烧字幕、可选 BGM。
    """
    temp_dir = temp_dir or output_video.parent
    temp_dir.mkdir(parents=True, exist_ok=True)

    normalized: List[Path] = []
    for i, p in enumerate(clip_paths):
        normalized.append(_normalize_clip_to_duration(p, seconds_per_clip, temp_dir, i))

    concat_list = temp_dir / "video_clips_concat.txt"
    with open(concat_list, "w") as f:
        for p in normalized:
            f.write(f"file '{p.absolute()}'\n")

    video_no_audio = temp_dir / "clips_concat.mp4"
    _run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24",
        "-movflags", "+faststart",
        str(video_no_audio),
    ])

    voice_concat_list = temp_dir / "voice_list.txt"
    with open(voice_concat_list, "w") as f:
        for i, ap in enumerate(audio_paths):
            if ap and ap.exists():
                f.write(f"file '{ap.absolute()}'\n")
            else:
                silent = temp_dir / f"silent_{i}.mp3"
                _run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(seconds_per_clip), "-q:a", "9", str(silent)])
                f.write(f"file '{silent.absolute()}'\n")
    voice_merged = temp_dir / "voice_merged.mp3"
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(voice_concat_list), "-c", "copy", str(voice_merged)])

    video_with_voice = temp_dir / "video_with_voice.mp4"
    _run_ffmpeg([
        "-i", str(video_no_audio),
        "-i", str(voice_merged),
        "-c:v", "copy", "-c:a", "aac", "-shortest",
        str(video_with_voice),
    ])

    srt_esc = str(srt_path.absolute()).replace("\\", "/").replace(":", "\\:")
    video_with_subs = temp_dir / "video_with_subs.mp4"
    _run_ffmpeg([
        "-i", str(video_with_voice),
        "-vf", f"subtitles='{srt_esc}':force_style='Fontsize=24,PrimaryColour=&Hffffff&,Outline=2'",
        "-c:a", "copy",
        str(video_with_subs),
    ])

    if bgm_path and bgm_path.exists():
        _run_ffmpeg([
            "-i", str(video_with_subs),
            "-i", str(bgm_path),
            "-filter_complex", "[1:a]volume=0.2[a1];[0:a][a1]amix=inputs=2:duration=shortest[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-shortest",
            str(output_video),
        ])
    else:
        shutil.copy2(video_with_subs, output_video)

    return output_video
