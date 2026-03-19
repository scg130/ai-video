"""
ComfyUI + CogVideoXWrapper 文生视频（本地）。
需在 ComfyUI 中安装 https://github.com/kijai/ComfyUI-CogVideoXWrapper，
在界面中跑通一次后「导出 API 格式」为 JSON，配置 COGVIDEOX_WORKFLOW_PATH。

调用方式与标准 ComfyUI 一致：POST /prompt → 轮询 /history → /view 下载输出（含 mp4/webm）。
"""
import asyncio
import json
import random
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

STYLE_PREFIX = "古风修仙，斩仙台，电影质感，竖屏，中国风，短剧，"


def _load_workflow() -> dict[str, Any]:
    path = (settings.cogvideox_workflow_path or "").strip()
    if not path:
        raise ValueError(
            "已启用 cogvideox 但未配置 COGVIDEOX_WORKFLOW_PATH（.env），请在 ComfyUI 中导出 API 格式 workflow JSON 并填写绝对路径"
        )
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"CogVideoX workflow 文件不存在: {p.absolute()}")
    return json.loads(p.read_text(encoding="utf-8"))


def _inject_prompts(workflow: dict[str, Any], positive: str, negative: str) -> dict[str, Any]:
    """深拷贝并注入正/负向提示词。优先使用配置的 node id，否则尝试匹配含 TextEncode 的节点。"""
    w = json.loads(json.dumps(workflow))
    pos = (STYLE_PREFIX + positive)[:4000]
    neg = (negative or settings.cogvideox_negative_default or "")[:4000]

    pid = (settings.cogvideox_prompt_node_id or "").strip()
    nid = (settings.cogvideox_negative_node_id or "").strip()

    if pid and pid in w and isinstance(w[pid], dict):
        inp = w[pid].setdefault("inputs", {})
        if "text" in inp:
            inp["text"] = pos
        elif "prompt" in inp:
            inp["prompt"] = pos

    if nid and nid in w and isinstance(w[nid], dict):
        inp = w[nid].setdefault("inputs", {})
        if "text" in inp:
            inp["text"] = neg
        elif "prompt" in inp:
            inp["prompt"] = neg

    if not pid:
        encoders: list[tuple[str, dict]] = []
        for node_id, node in w.items():
            if not isinstance(node, dict):
                continue
            ct = (node.get("class_type") or "")
            inp = node.get("inputs") or {}
            if "TextEncode" in ct or "text_encode" in ct.lower():
                if "text" in inp or "prompt" in inp:
                    encoders.append((str(node_id), node))
        if encoders:
            k0, n0 = encoders[0]
            i0 = n0.setdefault("inputs", {})
            if "text" in i0:
                i0["text"] = pos
            elif "prompt" in i0:
                i0["prompt"] = pos
            if len(encoders) > 1 and neg:
                _, n1 = encoders[1]
                i1 = n1.setdefault("inputs", {})
                if "text" in i1:
                    i1["text"] = neg
                elif "prompt" in i1:
                    i1["prompt"] = neg

    # 可选：仅对第一个含 Sampler 的节点写随机 seed，避免误改多节点
    if getattr(settings, "cogvideox_randomize_seed", True):
        seed = random.randint(0, 2**31 - 1)
        for node in w.values():
            if not isinstance(node, dict):
                continue
            ct = node.get("class_type") or ""
            inp = node.get("inputs") or {}
            if "Sampler" in ct and "seed" in inp:
                inp["seed"] = seed
                break

    return w


def _is_video_filename(name: str) -> bool:
    lower = name.lower()
    return lower.endswith((".mp4", ".webm", ".gif", ".avi", ".mov"))


async def _download_output_file(client: httpx.AsyncClient, base: str, img: dict) -> bytes:
    filename = img.get("filename", "")
    subfolder = img.get("subfolder", "")
    img_type = img.get("type", "output")
    from urllib.parse import quote

    q = f"filename={quote(filename)}&subfolder={quote(subfolder)}&type={quote(img_type)}"
    r = await client.get(f"{base}/view?{q}")
    r.raise_for_status()
    return r.content


async def generate_video_clip(prompt: str, out_path: Path, negative: str = "") -> None:
    """
    提交 CogVideoX workflow，等待完成，将输出的视频（或首帧图）保存到 out_path。
    out_path 建议后缀 .mp4。
    """
    base = (settings.comfyui_base_url or "http://127.0.0.1:8188").rstrip("/")
    workflow = _inject_prompts(_load_workflow(), prompt, negative)

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=3600.0, write=30.0, pool=30.0)) as client:
        r = await client.post(
            f"{base}/prompt",
            json={"prompt": workflow, "client_id": "ai-video-cogvideox"},
        )
        r.raise_for_status()
        data = r.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI 未返回 prompt_id: {data}")
        if data.get("node_errors"):
            raise RuntimeError(f"ComfyUI workflow 节点错误: {data['node_errors']}")

        for _ in range(3600):
            await asyncio.sleep(2)
            hr = await client.get(f"{base}/history/{prompt_id}")
            hr.raise_for_status()
            h = hr.json()
            if prompt_id not in h:
                continue
            outputs = h[prompt_id].get("outputs") or {}
            # 优先找视频文件
            for _node_id, out in outputs.items():
                for img in out.get("images") or []:
                    fn = img.get("filename", "")
                    if _is_video_filename(fn):
                        content = await _download_output_file(client, base, img)
                        out_path.write_bytes(content)
                        return
                for vid in out.get("videos") or []:
                    content = await _download_output_file(client, base, vid)
                    out_path.write_bytes(content)
                    return
            # 回退：首张 PNG（部分工作流只出图）
            for _node_id, out in outputs.items():
                for img in out.get("images") or []:
                    fn = img.get("filename", "")
                    if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        content = await _download_output_file(client, base, img)
                        # 若期望 mp4 但得到图，仍写入，后续 pipeline 可检测扩展名
                        out_path.write_bytes(content)
                        return

        raise RuntimeError("CogVideoX / ComfyUI 执行超时，未在输出中找到视频或图片")


async def generate_cogvideox_clips_for_scenes(scenes: list[dict], out_dir: Path) -> list[Path]:
    """每个分镜生成一段短视频，返回路径列表（通常为 .mp4）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    neg = settings.sd_negative_prompt or settings.cogvideox_negative_default
    for i, s in enumerate(scenes):
        scene_desc = (s.get("scene") or "").strip() or "修仙场景，云雾缭绕"
        emotion = (s.get("emotion") or "").strip()
        prompt = f"{scene_desc}，人物情绪：{emotion}" if emotion else scene_desc
        path = out_dir / f"scene_{i:03d}.mp4"
        await generate_video_clip(prompt, path, negative=neg)
        paths.append(path)
    return paths
