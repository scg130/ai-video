"""TTS：分镜台词 → 配音音频。支持内部 API 或 OpenAI TTS。"""
import asyncio
from pathlib import Path
from typing import Optional

import httpx
from openai import AsyncOpenAI

from app.config import settings


async def _tts_openai(
    texts: list[str],
    emotion_hints: Optional[list[str]] = None,
    out_dir: Path = None,
) -> list[Path]:
    """用 OpenAI TTS 按句生成多个音频文件，返回路径列表。"""
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    paths = []
    # 情绪可映射到不同 voice，这里简化用同一 voice
    voice = "alloy"
    for i, text in enumerate(texts):
        if not text or not text.strip():
            # 静音或占位：可生成 0.5s 静音或跳过，这里跳过由视频层用图时长补
            paths.append(None)
            continue
        path = out_dir / f"tts_{i:03d}.mp3"
        resp = await client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text[:4096],
        )
        path.write_bytes(resp.content)
        paths.append(path)
    return paths


async def _tts_internal_api(
    texts: list[str],
    emotion_hints: Optional[list[str]] = None,
    out_dir: Path = None,
) -> list[Path]:
    """调用内部 TTS API（你 curl 那套）。按句请求，返回每句的音频路径。"""
    base = (settings.tts_base_url or "").rstrip("/")
    if not base:
        raise ValueError("TTS_BASE_URL 未配置，请使用 USE_OPENAI_TTS=true 或配置内部 TTS")
    paths = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i, text in enumerate(texts):
            if not text or not text.strip():
                paths.append(None)
                continue
            path = out_dir / f"tts_{i:03d}.mp3"
            # 按你现有接口调整：例如 POST /tts { "text": "...", "voice_id": "xxx" }
            r = await client.post(
                f"{base}/tts",
                json={"text": text[:4096], "emotion": (emotion_hints or [""])[i] if emotion_hints else ""},
                headers={"Authorization": f"Bearer {settings.tts_api_key}"} if settings.tts_api_key else {},
            )
            r.raise_for_status()
            path.write_bytes(r.content)
            paths.append(path)
    return paths


async def generate_tts_for_scenes(
    scenes: list[dict],
    out_dir: Path,
) -> list[Path]:
    """
    根据分镜生成配音。每个分镜一句，返回对应音频文件路径列表（无台词可为 None）。
    """
    texts = [s.get("dialogue", "").strip() for s in scenes]
    emotions = [s.get("emotion", "") for s in scenes]
    if settings.use_openai_tts:
        return await _tts_openai(texts, emotions, out_dir)
    return await _tts_internal_api(texts, emotions, out_dir)
