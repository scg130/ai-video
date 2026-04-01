"""TTS：分镜台词 → 配音；支持按 role 映射不同声线（OpenAI / 内部 API）。"""
import asyncio
from pathlib import Path
from typing import Optional

import httpx
from openai import AsyncOpenAI

from app.config import settings
from app.services.model_debug_io import print_model_io
from app.services.openai_keys import async_run_with_key_rotation, openai_sdk_base_url_kwargs

# OpenAI TTS voice：主角低沉、反派偏锐、女主柔和、旁白中性
ROLE_TO_VOICE = {
    "主角": "onyx",
    "反派": "echo",
    "女主": "shimmer",
    "路人": "alloy",
    "旁白": "nova",
}


def _voice_for_role(role: str) -> str:
    r = (role or "").strip()
    return ROLE_TO_VOICE.get(r, settings.tts_default_voice)


async def _tts_openai(
    texts: list[str],
    roles: Optional[list[str]] = None,
    emotion_hints: Optional[list[str]] = None,
    out_dir: Path = None,
) -> list[Path]:
    roles = roles or []

    tolerant = getattr(settings, "pipeline_fault_tolerant", True)

    async def _run(api_key: str) -> list[Path]:
        client = AsyncOpenAI(api_key=api_key, **openai_sdk_base_url_kwargs())
        paths: list[Path] = []
        for i, text in enumerate(texts):
            if not text or not text.strip():
                paths.append(None)
                continue
            role = roles[i] if i < len(roles) else ""
            voice = _voice_for_role(role)
            path = out_dir / f"tts_{i:03d}.mp3"
            try:
                resp = await client.audio.speech.create(
                    model="tts-1",
                    voice=voice,
                    input=text[:4096],
                )
                audio = resp.content
                path.write_bytes(audio)
                print_model_io(
                    f"OpenAI TTS 分镜[{i}]",
                    f"model=tts-1 voice={voice}\ninput=\n{text[:4096]}",
                    f"file={path}\nbytes={len(audio)}",
                )
                paths.append(path)
            except Exception:
                if tolerant:
                    paths.append(None)
                else:
                    raise
        return paths

    return await async_run_with_key_rotation(_run, what="OpenAI TTS")


async def _tts_internal_api(
    texts: list[str],
    roles: Optional[list[str]] = None,
    emotion_hints: Optional[list[str]] = None,
    out_dir: Path = None,
) -> list[Path]:
    base = (settings.tts_base_url or "").rstrip("/")
    if not base:
        raise ValueError("TTS_BASE_URL 未配置，请使用 USE_OPENAI_TTS=true 或配置内部 TTS")
    paths = []
    roles = roles or []
    tolerant = getattr(settings, "pipeline_fault_tolerant", True)
    async with httpx.AsyncClient(timeout=120.0) as client:
        for i, text in enumerate(texts):
            if not text or not text.strip():
                paths.append(None)
                continue
            path = out_dir / f"tts_{i:03d}.mp3"
            role = roles[i] if i < len(roles) else "主角"
            emotion = (emotion_hints or [""])[i] if emotion_hints else ""
            payload = {"text": text[:4096], "emotion": emotion, "role": role}
            try:
                r = await client.post(
                    f"{base}/tts",
                    json=payload,
                    headers={"Authorization": f"Bearer {settings.tts_api_key}"} if settings.tts_api_key else {},
                )
                r.raise_for_status()
                audio = r.content
                path.write_bytes(audio)
                print_model_io(
                    f"内部TTS API 分镜[{i}]",
                    f"POST {base}/tts\n{payload}",
                    f"file={path}\nbytes={len(audio)}",
                )
                paths.append(path)
            except Exception:
                if tolerant:
                    paths.append(None)
                else:
                    raise
    return paths


async def generate_tts_for_scenes(
    scenes: list[dict],
    out_dir: Path,
) -> list[Path]:
    """按分镜生成配音；使用 scene['role'] 分角色。"""
    texts = [s.get("dialogue", "").strip() for s in scenes]
    emotions = [s.get("emotion", "") for s in scenes]
    roles = [s.get("role", "主角") for s in scenes]
    if settings.use_openai_tts:
        return await _tts_openai(texts, roles, emotions, out_dir)
    return await _tts_internal_api(texts, roles, emotions, out_dir)
