"""
短剧流水线 LangGraph：init → 剧本 → 字幕 →（按 VISUAL_MODE 分支）媒体 → 合成 → 封面。

与原先 pipeline_service 行为一致，便于后续加检查点、人机介入、子图扩展。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from app.config import settings
from app.services.media_fallback import placeholder_mp4
from app.services.script_service import (
    generate_script,
    normalize_scenes_list,
    qwen_configured,
    script_llm_mode_is_local,
)
from app.services.subtitle_service import to_srt
from app.services.tts_service import generate_tts_for_scenes
from app.services.image_service import _use_sd_webui, generate_images_for_scenes
from app.services.visual_prompt import build_visual_prompt
from app.services.comfyui_cogvideox_service import generate_video_clip as cog_generate_clip
from app.services.comfyui_animatediff_service import generate_animatediff_clip as ad_generate_clip
from app.services.video_service import (
    build_video,
    build_video_from_clips,
    enhance_cover_with_title,
    segment_durations_from_scenes,
)
from app.services.pipeline_service import (
    _cover_from_first_clip,
    _pipeline_tolerant,
    _promo_title_from_scenes,
)

_log = logging.getLogger(__name__)


class DramaState(TypedDict, total=False):
    theme: str
    style: str
    duration: int
    job_id: str
    bgm_path: Optional[str]
    scenes: list[dict[str, Any]]
    series_id: Optional[str]
    episode: int
    temp_dir: str
    out_dir: str
    visual: str
    durs: list[float]
    srt_path: str
    output_video: str
    cover_path: str
    cover_base: str
    tts_paths: list[Optional[str]]
    clip_paths: list[str]
    image_paths: list[str]


def _bgm(state: DramaState) -> Optional[Path]:
    p = state.get("bgm_path")
    return Path(p) if p else None


async def node_init_paths(state: DramaState) -> dict[str, Any]:
    jid = state.get("job_id") or uuid.uuid4().hex[:12]
    temp_dir = settings.temp_path / jid
    temp_dir.mkdir(parents=True, exist_ok=True)
    out_dir = settings.output_path / jid
    out_dir.mkdir(parents=True, exist_ok=True)
    return {
        "job_id": jid,
        "temp_dir": str(temp_dir),
        "out_dir": str(out_dir),
        "output_video": str(out_dir / "short_drama.mp4"),
        "cover_path": str(out_dir / "cover.png"),
        "cover_base": str(temp_dir / "cover_base.png"),
        "visual": (settings.visual_mode or "images").lower().strip(),
    }


async def node_load_script(state: DramaState) -> dict[str, Any]:
    raw = state.get("scenes")
    if raw is not None:
        scenes = normalize_scenes_list(raw)
    else:
        loop = asyncio.get_event_loop()
        try:
            scenes = await loop.run_in_executor(
                None,
                lambda: generate_script(
                    theme=state["theme"],
                    style=state["style"],
                    duration=state["duration"],
                    series_id=state.get("series_id"),
                    episode=max(1, int(state.get("episode") or 1)),
                ),
            )
        except Exception:
            if (
                _pipeline_tolerant()
                and qwen_configured()
                and not script_llm_mode_is_local()
            ):
                scenes = await loop.run_in_executor(
                    None,
                    lambda: generate_script(
                        theme=state["theme"],
                        style=state["style"],
                        duration=state["duration"],
                        series_id=state.get("series_id"),
                        episode=max(1, int(state.get("episode") or 1)),
                        qwen_only=True,
                    ),
                )
            else:
                raise
    durs = segment_durations_from_scenes(scenes, default=5.0)
    return {"scenes": scenes, "durs": durs}


async def node_subtitles(state: DramaState) -> dict[str, Any]:
    temp_dir = Path(state["temp_dir"])
    srt_path = temp_dir / "subs.srt"
    srt_path.write_text(to_srt(state["scenes"]), encoding="utf-8")
    return {"srt_path": str(srt_path)}


def route_visual(state: DramaState) -> Literal["media_cog", "media_ad", "media_img"]:
    v = (state.get("visual") or "images").lower().strip()
    jid = state.get("job_id", "")
    if v == "cogvideox":
        _log.info(
            "[画面] 路由 VISUAL_MODE=cogvideox → 节点 media_cog（ComfyUI 文生视频）job_id=%s",
            jid,
        )
        return "media_cog"
    if v == "animatediff":
        _log.info(
            "[画面] 路由 VISUAL_MODE=animatediff → 节点 media_ad（ComfyUI AnimateDiff）job_id=%s",
            jid,
        )
        return "media_ad"
    _log.info(
        "[画面] 路由 VISUAL_MODE=images → 节点 media_img（文生图）job_id=%s",
        jid,
    )
    return "media_img"


async def node_media_cog(state: DramaState) -> dict[str, Any]:
    temp_dir = Path(state["temp_dir"])
    scenes = state["scenes"]
    durs = state["durs"]
    jid = state.get("job_id", "")
    neg = settings.sd_negative_prompt or settings.cogvideox_negative_default
    _log.info(
        "[画面] cogvideox 开始 job_id=%s 分镜数=%d comfyui=%s workflow=%s",
        jid,
        len(scenes),
        settings.comfyui_base_url,
        (settings.cogvideox_workflow_path or "")[:120] or "(未配置)",
    )

    async def _cog_clips() -> list[Path]:
        out: list[Path] = []
        for i, s in enumerate(scenes):
            p = temp_dir / f"scene_{i:03d}.mp4"
            vp = build_visual_prompt(s)
            _log.info(
                "[画面] cogvideox 分镜 [%d/%d] prompt前80字=%r → %s",
                i + 1,
                len(scenes),
                (vp[:80] + "…") if len(vp) > 80 else vp,
                p.name,
            )
            try:
                await cog_generate_clip(vp, p, negative=neg)
                if not p.exists() or p.stat().st_size < 64:
                    raise RuntimeError("CogVideoX 输出无效")
                _log.info(
                    "[画面] cogvideox 分镜 [%d/%d] 完成 bytes=%d",
                    i + 1,
                    len(scenes),
                    p.stat().st_size,
                )
            except Exception as e:
                if _pipeline_tolerant():
                    _log.warning(
                        "[画面] cogvideox 分镜 [%d/%d] 失败，使用占位: %s",
                        i + 1,
                        len(scenes),
                        e,
                    )
                    placeholder_mp4(p, durs[i] if i < len(durs) else 5.0)
                else:
                    raise
            out.append(p)
        return out

    tts_paths, clip_paths = await asyncio.gather(
        generate_tts_for_scenes(scenes, temp_dir),
        _cog_clips(),
    )
    _log.info(
        "[画面] cogvideox 结束 job_id=%s 片段=%d TTS=%d",
        jid,
        len(clip_paths),
        sum(1 for x in tts_paths if x),
    )
    return {
        "tts_paths": [str(x) if x else None for x in tts_paths],
        "clip_paths": [str(p) for p in clip_paths],
    }


async def node_media_ad(state: DramaState) -> dict[str, Any]:
    temp_dir = Path(state["temp_dir"])
    scenes = state["scenes"]
    durs = state["durs"]
    jid = state.get("job_id", "")
    neg = settings.sd_negative_prompt
    _log.info(
        "[画面] animatediff 开始 job_id=%s 分镜数=%d comfyui=%s workflow=%s",
        jid,
        len(scenes),
        settings.comfyui_base_url,
        (settings.animatediff_workflow_path or "")[:120] or "(未配置)",
    )

    async def _ad_clips() -> list[Path]:
        out: list[Path] = []
        for i, s in enumerate(scenes):
            p = temp_dir / f"scene_{i:03d}.mp4"
            vp = build_visual_prompt(s)
            _log.info(
                "[画面] animatediff 分镜 [%d/%d] prompt前80字=%r → %s",
                i + 1,
                len(scenes),
                (vp[:80] + "…") if len(vp) > 80 else vp,
                p.name,
            )
            try:
                await ad_generate_clip(vp, p, negative=neg)
                if not p.exists() or p.stat().st_size < 64:
                    raise RuntimeError("AnimateDiff 输出无效")
                _log.info(
                    "[画面] animatediff 分镜 [%d/%d] 完成 bytes=%d",
                    i + 1,
                    len(scenes),
                    p.stat().st_size,
                )
            except Exception as e:
                if _pipeline_tolerant():
                    _log.warning(
                        "[画面] animatediff 分镜 [%d/%d] 失败，使用占位: %s",
                        i + 1,
                        len(scenes),
                        e,
                    )
                    placeholder_mp4(p, durs[i] if i < len(durs) else 5.0)
                else:
                    raise
            out.append(p)
        return out

    tts_paths, clip_paths = await asyncio.gather(
        generate_tts_for_scenes(scenes, temp_dir),
        _ad_clips(),
    )
    _log.info(
        "[画面] animatediff 结束 job_id=%s 片段=%d TTS=%d",
        jid,
        len(clip_paths),
        sum(1 for x in tts_paths if x),
    )
    return {
        "tts_paths": [str(x) if x else None for x in tts_paths],
        "clip_paths": [str(p) for p in clip_paths],
    }


async def node_media_img(state: DramaState) -> dict[str, Any]:
    temp_dir = Path(state["temp_dir"])
    scenes = state["scenes"]
    jid = state.get("job_id", "")
    provider = "sd_webui" if _use_sd_webui() else "openai_dalle"
    _log.info(
        "[画面] images 开始 job_id=%s 分镜数=%d 文生图通道=%s",
        jid,
        len(scenes),
        provider,
    )
    tts_paths, image_paths = await asyncio.gather(
        generate_tts_for_scenes(scenes, temp_dir),
        generate_images_for_scenes(scenes, temp_dir),
    )
    _log.info(
        "[画面] images 结束 job_id=%s 图片=%d TTS=%d",
        jid,
        len(image_paths),
        sum(1 for x in tts_paths if x),
    )
    return {
        "tts_paths": [str(x) if x else None for x in tts_paths],
        "image_paths": [str(p) for p in image_paths],
    }


async def node_assemble_clips(state: DramaState) -> dict[str, Any]:
    jid = state.get("job_id", "")
    _log.info(
        "[合成视频] 节点 assemble_clips(视频片段) 开始 job_id=%s clips=%d → %s",
        jid,
        len(state.get("clip_paths") or []),
        state.get("output_video"),
    )
    clip_paths = [Path(p) for p in state["clip_paths"]]
    tts_paths: list[Optional[Path]] = [Path(p) if p else None for p in state["tts_paths"]]
    temp_dir = Path(state["temp_dir"])
    cover_base = Path(state["cover_base"])
    _cover_from_first_clip(clip_paths, cover_base)
    build_video_from_clips(
        clip_paths=clip_paths,
        audio_paths=tts_paths,
        scenes=state["scenes"],
        srt_path=Path(state["srt_path"]),
        output_video=Path(state["output_video"]),
        bgm_path=_bgm(state),
        seconds_per_clip=5.0,
        temp_dir=temp_dir,
        segment_durations=state["durs"],
    )
    _log.info("[合成视频] 节点 assemble_clips 结束 job_id=%s", jid)
    return {}


async def node_assemble_images(state: DramaState) -> dict[str, Any]:
    jid = state.get("job_id", "")
    _log.info(
        "[合成视频] 节点 assemble_images(静图) 开始 job_id=%s images=%d → %s",
        jid,
        len(state.get("image_paths") or []),
        state.get("output_video"),
    )
    image_paths = [Path(p) for p in state["image_paths"]]
    tts_paths = [Path(p) if p else None for p in state["tts_paths"]]
    temp_dir = Path(state["temp_dir"])
    cover_base = Path(state["cover_base"])
    if image_paths:
        shutil.copy2(image_paths[0], cover_base)
    build_video(
        image_paths=image_paths,
        audio_paths=tts_paths,
        scenes=state["scenes"],
        srt_path=Path(state["srt_path"]),
        output_video=Path(state["output_video"]),
        bgm_path=_bgm(state),
        seconds_per_image=5.0,
        temp_dir=temp_dir,
        segment_durations=state["durs"],
    )
    _log.info("[合成视频] 节点 assemble_images 结束 job_id=%s", jid)
    return {}


async def node_finalize_cover(state: DramaState) -> dict[str, Any]:
    cover_base = Path(state["cover_base"])
    cover_path = Path(state["cover_path"])
    temp_dir = Path(state["temp_dir"])
    scenes = state["scenes"]
    if getattr(settings, "cover_promo_title", True) and cover_base.exists():
        try:
            enhance_cover_with_title(
                cover_base, _promo_title_from_scenes(scenes), cover_path, temp_dir
            )
        except Exception:
            shutil.copy2(cover_base, cover_path)
    elif cover_base.exists():
        shutil.copy2(cover_base, cover_path)
    return {}


def build_drama_graph() -> Any:
    g = StateGraph(DramaState)
    g.add_node("init_paths", node_init_paths)
    g.add_node("load_script", node_load_script)
    g.add_node("subtitles", node_subtitles)
    g.add_node("media_cog", node_media_cog)
    g.add_node("media_ad", node_media_ad)
    g.add_node("media_img", node_media_img)
    g.add_node("assemble_clips", node_assemble_clips)
    g.add_node("assemble_images", node_assemble_images)
    g.add_node("finalize_cover", node_finalize_cover)

    g.add_edge(START, "init_paths")
    g.add_edge("init_paths", "load_script")
    g.add_edge("load_script", "subtitles")
    g.add_conditional_edges(
        "subtitles",
        route_visual,
        {
            "media_cog": "media_cog",
            "media_ad": "media_ad",
            "media_img": "media_img",
        },
    )
    g.add_edge("media_cog", "assemble_clips")
    g.add_edge("media_ad", "assemble_clips")
    g.add_edge("media_img", "assemble_images")
    g.add_edge("assemble_clips", "finalize_cover")
    g.add_edge("assemble_images", "finalize_cover")
    g.add_edge("finalize_cover", END)
    return g


_compiled_graph: Any = None


def get_compiled_drama_graph() -> Any:
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_drama_graph().compile()
    return _compiled_graph


async def invoke_drama_pipeline(
    theme: str,
    style: str,
    duration: int,
    bgm_path: Optional[Path] = None,
    job_id: Optional[str] = None,
    scenes: Optional[list[dict[str, Any]]] = None,
    series_id: Optional[str] = None,
    episode: int = 1,
) -> tuple[Path, Path, list[dict[str, Any]]]:
    """对外入口：与 pipeline_service.run_pipeline 相同返回值。"""
    initial: DramaState = {
        "theme": theme,
        "style": style,
        "duration": duration,
        "bgm_path": str(bgm_path) if bgm_path else None,
        "series_id": series_id,
        "episode": episode,
    }
    if job_id:
        initial["job_id"] = job_id
    if scenes is not None:
        initial["scenes"] = scenes

    graph = get_compiled_drama_graph()
    final = await graph.ainvoke(initial)
    return (
        Path(final["output_video"]),
        Path(final["cover_path"]),
        final["scenes"],
    )
