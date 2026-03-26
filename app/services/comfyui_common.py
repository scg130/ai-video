"""ComfyUI API 公共逻辑：加载 workflow、注入 prompt/seed、提交并拉取视频/图。"""
import asyncio
import json
import random
from pathlib import Path
from typing import Any

import httpx

DRAMA_STYLE_PREFIX = "古风修仙，斩仙台，电影质感，竖屏，中国风，短剧，"


def load_workflow_json(path: str, env_hint: str) -> dict[str, Any]:
    p = (path or "").strip()
    if not p:
        raise ValueError(f"未配置 workflow 路径（.env: {env_hint}），请在 ComfyUI 中导出 API 格式 JSON")
    fp = Path(p)
    if not fp.is_file():
        raise FileNotFoundError(f"workflow 文件不存在: {fp.absolute()}")
    return json.loads(fp.read_text(encoding="utf-8"))


def inject_prompts(
    workflow: dict[str, Any],
    positive: str,
    negative: str,
    *,
    prompt_node_id: str,
    negative_node_id: str,
    default_negative: str,
    style_prefix: str = DRAMA_STYLE_PREFIX,
    prepend_style_prefix: bool = True,
) -> dict[str, Any]:
    """注入正/负向词；未配置 node id 时按 CLIPTextEncode / TextEncode 顺序猜测。"""
    w = json.loads(json.dumps(workflow))
    pos = ((style_prefix + positive) if prepend_style_prefix else positive)[:4000]
    neg = (negative or default_negative or "")[:4000]

    pid = (prompt_node_id or "").strip()
    nid = (negative_node_id or "").strip()

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
            if "CLIPTextEncode" in ct or "TextEncode" in ct or "text_encode" in ct.lower():
                if "text" in inp or "prompt" in inp:
                    encoders.append((str(node_id), node))
        def _node_sort_key(t: tuple[str, dict]) -> tuple[int, str]:
            k = t[0]
            return (int(k) if str(k).isdigit() else 0, k)

        encoders.sort(key=_node_sort_key)
        if encoders:
            _, n0 = encoders[0]
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

    return w


def inject_sampler_seed(workflow: dict[str, Any], randomize: bool) -> None:
    if not randomize:
        return
    seed = random.randint(0, 2**31 - 1)
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type") or ""
        inp = node.get("inputs") or {}
        if "Sampler" in ct and "seed" in inp:
            inp["seed"] = seed
            break


def is_video_filename(name: str) -> bool:
    lower = name.lower()
    return lower.endswith((".mp4", ".webm", ".gif", ".avi", ".mov"))


async def download_view(client: httpx.AsyncClient, base: str, img: dict) -> bytes:
    from urllib.parse import quote

    filename = img.get("filename", "")
    subfolder = img.get("subfolder", "")
    img_type = img.get("type", "output")
    q = f"filename={quote(filename)}&subfolder={quote(subfolder)}&type={quote(img_type)}"
    r = await client.get(f"{base}/view?{q}")
    r.raise_for_status()
    return r.content


async def run_workflow_save_output(
    base_url: str,
    workflow: dict[str, Any],
    out_path: Path,
    client_id: str,
    timeout_label: str = "ComfyUI",
) -> None:
    """POST /prompt，轮询 history，将首个视频或首张图写入 out_path。"""
    base = (base_url or "http://127.0.0.1:8188").rstrip("/")
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=3600.0, write=30.0, pool=30.0)) as client:
        r = await client.post(
            f"{base}/prompt",
            json={"prompt": workflow, "client_id": client_id},
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
            for _node_id, out in outputs.items():
                for img in out.get("images") or []:
                    fn = img.get("filename", "")
                    if is_video_filename(fn):
                        out_path.write_bytes(await download_view(client, base, img))
                        return
                for vid in out.get("videos") or []:
                    out_path.write_bytes(await download_view(client, base, vid))
                    return
            for _node_id, out in outputs.items():
                for img in out.get("images") or []:
                    fn = img.get("filename", "")
                    if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        out_path.write_bytes(await download_view(client, base, img))
                        return

        raise RuntimeError(f"{timeout_label} 执行超时，未在输出中找到视频或图片")
