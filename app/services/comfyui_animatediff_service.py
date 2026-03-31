"""
ComfyUI + AnimateDiff 文生视频（本地）。

典型链路（需在 ComfyUI 中自行搭好并导出 API）：
  Prompt（CLIP 编码）
    → Load Checkpoint
    → AnimateDiff Loader（或 AnimateDiffModuleLoader 等，依扩展版本）
    → KSampler
    → Video Combine（VHS / AnimateDiff 等输出 gif/mp4）

常见扩展：
  - https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved
  - 或社区其他 AnimateDiff 节点包

配置 ANIMATEDIFF_WORKFLOW_PATH 指向导出的 API JSON。
"""
import logging
from pathlib import Path

from app.config import settings
from app.services import comfyui_common as cc
from app.services.visual_prompt import build_visual_prompt

_log = logging.getLogger(__name__)


async def generate_animatediff_clip(prompt: str, out_path: Path, negative: str = "") -> None:
    _log.info(
        "[画面] AnimateDiff 调用开始 base=%s → %s prompt前100字=%r",
        settings.comfyui_base_url,
        out_path.name,
        (prompt[:100] + "…") if len(prompt) > 100 else prompt,
    )
    wf = cc.load_workflow_json(settings.animatediff_workflow_path, "ANIMATEDIFF_WORKFLOW_PATH")
    w = cc.inject_prompts(
        wf,
        prompt,
        negative,
        prompt_node_id=settings.animatediff_prompt_node_id,
        negative_node_id=settings.animatediff_negative_node_id,
        default_negative=settings.sd_negative_prompt,
        prepend_style_prefix=False,
    )
    cc.inject_sampler_seed(w, settings.animatediff_randomize_seed)
    await cc.run_workflow_save_output(
        settings.comfyui_base_url,
        w,
        out_path,
        client_id="ai-video-animatediff",
        timeout_label="AnimateDiff",
    )
    sz = out_path.stat().st_size if out_path.exists() else 0
    _log.info("[画面] AnimateDiff 调用结束 %s bytes=%d", out_path.name, sz)


async def generate_animatediff_clips_for_scenes(scenes: list[dict], out_dir: Path) -> list[Path]:
    """每个分镜调用一次 AnimateDiff workflow，生成一段动图/视频。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    neg = settings.sd_negative_prompt
    for i, s in enumerate(scenes):
        prompt = build_visual_prompt(s)
        path = out_dir / f"scene_{i:03d}.mp4"
        await generate_animatediff_clip(prompt, path, negative=neg)
        paths.append(path)
    return paths
