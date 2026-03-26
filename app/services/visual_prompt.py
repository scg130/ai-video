"""统一画面 Prompt：仙侠电影感 + 分镜 scene / emotion / camera。"""
from app.config import settings


def build_visual_prompt(scene: dict) -> str:
    """
    文生图 / ComfyUI 共用。
    scene 可含：scene, emotion, camera, role（仅文案用，可不进图）。
    """
    desc = (scene.get("scene") or "").strip() or "修仙场景，云雾缭绕"
    emotion = (scene.get("emotion") or "").strip()
    camera = (scene.get("camera") or "").strip()
    suffix = (settings.visual_prompt_suffix or "").strip()
    parts = [
        "仙侠风格，电影级画面，光影强烈，",
        desc,
    ]
    if emotion:
        parts.append(f"，{emotion}")
    if camera:
        parts.append(f"，镜头：{camera}")
    parts.append("，cinematic lighting, ultra detail, 4k")
    if suffix:
        parts.append(f"，{suffix}")
    return "".join(parts)[:4000]
