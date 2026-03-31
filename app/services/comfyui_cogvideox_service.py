"""
ComfyUI + CogVideoXWrapper 文生视频（本地）。
需在 ComfyUI 中安装 https://github.com/kijai/ComfyUI-CogVideoXWrapper，
导出 API 格式 workflow，配置 COGVIDEOX_WORKFLOW_PATH。
"""
import logging
from pathlib import Path

from app.config import settings
from app.services import comfyui_common as cc
from app.services.visual_prompt import build_visual_prompt

_log = logging.getLogger(__name__)


async def generate_video_clip(prompt: str, out_path: Path, negative: str = "") -> None:
    _log.info(
        "[画面] CogVideoX 调用开始 base=%s → %s prompt前100字=%r",
        settings.comfyui_base_url,
        out_path.name,
        (prompt[:100] + "…") if len(prompt) > 100 else prompt,
    )
    wf = cc.load_workflow_json(settings.cogvideox_workflow_path, "COGVIDEOX_WORKFLOW_PATH")
    default_neg = settings.cogvideox_negative_default or settings.sd_negative_prompt
    w = cc.inject_prompts(
        wf,
        prompt,
        negative,
        prompt_node_id=settings.cogvideox_prompt_node_id,
        negative_node_id=settings.cogvideox_negative_node_id,
        default_negative=default_neg,
        prepend_style_prefix=False,
    )
    cc.inject_sampler_seed(w, settings.cogvideox_randomize_seed)
    await cc.run_workflow_save_output(
        settings.comfyui_base_url,
        w,
        out_path,
        client_id="ai-video-cogvideox",
        timeout_label="CogVideoX",
    )
    sz = out_path.stat().st_size if out_path.exists() else 0
    _log.info("[画面] CogVideoX 调用结束 %s bytes=%d", out_path.name, sz)


async def generate_cogvideox_clips_for_scenes(scenes: list[dict], out_dir: Path) -> list[Path]:
    """每个分镜生成一段短视频。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    neg = settings.sd_negative_prompt or settings.cogvideox_negative_default
    for i, s in enumerate(scenes):
        prompt = build_visual_prompt(s)
        path = out_dir / f"scene_{i:03d}.mp4"
        await generate_video_clip(prompt, path, negative=neg)
        paths.append(path)
    return paths
