"""文生图：OpenAI DALL·E 或本地 SD（AUTOMATIC1111 WebUI API）。"""
import base64
from pathlib import Path

import httpx
from openai import AsyncOpenAI

from app.config import settings

# 统一提示前缀：斩仙台风格，适合短视频竖屏
STYLE_PREFIX = "古风修仙，斩仙台，电影质感，竖屏构图，高清，8k，中国风，短剧风格，"


def _use_sd_webui() -> bool:
    p = (settings.image_provider or "").lower().strip()
    if p == "sd_webui":
        return True
    if p == "openai":
        return False
    # 未写 IMAGE_PROVIDER 时兼容旧变量 USE_OPENAI_IMAGE=false → 走本地 SD
    return not settings.use_openai_image


async def _generate_image_openai(prompt: str, out_path: Path) -> None:
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    full_prompt = STYLE_PREFIX + prompt
    resp = await client.images.generate(
        model="dall-e-3",
        prompt=full_prompt[:4000],
        size="1024x1792",
        quality="standard",
        n=1,
    )
    url = resp.data[0].url
    if not url:
        raise RuntimeError("OpenAI 未返回图片 URL")
    async with httpx.AsyncClient() as client_http:
        r = await client_http.get(url)
        r.raise_for_status()
        out_path.write_bytes(r.content)


async def _generate_image_sd_webui(prompt: str, out_path: Path) -> None:
    """
    调用本地 AUTOMATIC1111 WebUI 的 txt2img API。
    需先启动 WebUI 并加参数：--api（默认端口 7860）。
    文档：https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/API
    """
    base = (settings.sd_webui_base_url or "http://127.0.0.1:7860").rstrip("/")
    url = f"{base}/sdapi/v1/txt2img"
    full_prompt = (STYLE_PREFIX + prompt)[:2000]
    payload = {
        "prompt": full_prompt,
        "negative_prompt": settings.sd_negative_prompt[:2000],
        "steps": settings.sd_steps,
        "width": settings.sd_width,
        "height": settings.sd_height,
        "cfg_scale": settings.sd_cfg_scale,
        "sampler_name": settings.sd_sampler_name,
        "batch_size": 1,
        "n_iter": 1,
    }
    async with httpx.AsyncClient(timeout=600.0) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    images = data.get("images") or []
    if not images:
        raise RuntimeError("本地 SD 未返回图片，请检查 WebUI 是否开启 --api、模型是否已加载")
    raw = images[0]
    if "," in raw and raw.strip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    out_path.write_bytes(base64.b64decode(raw))


async def generate_images_for_scenes(scenes: list[dict], out_dir: Path) -> list[Path]:
    """
    为每个分镜生成一张图。prompt 用 scene 描述 + 可选 emotion。
    根据配置走 OpenAI 或本地 SD WebUI。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    gen = _generate_image_sd_webui if _use_sd_webui() else _generate_image_openai
    paths = []
    for i, s in enumerate(scenes):
        scene_desc = (s.get("scene") or "").strip() or "修仙场景，云雾缭绕"
        emotion = (s.get("emotion") or "").strip()
        if emotion:
            prompt = f"{scene_desc}，人物情绪：{emotion}"
        else:
            prompt = scene_desc
        path = out_dir / f"scene_{i:03d}.png"
        await gen(prompt, path)
        paths.append(path)
    return paths
