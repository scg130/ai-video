"""剧本：可选两步生成（大纲 → 分镜+镜头语言），接 RAG 参考。"""
import json
import random
import re
import time
from typing import Any, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings
from app.services import rag_service
from app.services.openai_keys import run_with_key_rotation
from app.services.visual_prompt import build_visual_prompt

DIRECTOR_SYSTEM = """你是短视频爽文导演，擅长抖音爆款修仙复仇剧情。

硬性要求：
1. 每约 5 秒要有反转或冲突张力，节奏像短视频不像长篇小说。
2. 台词尽量短（单句建议 10 字以内），口语、狠、好记。
3. 情绪要外放：愤怒、绝望、冷笑、逆袭、杀意等，避免平淡叙述。
4. 每个分镜必须带镜头感：如特写、俯视、慢推、跟拍、仰拍等（写在 camera 字段）。
5. 结构上必须有羞辱 → 反击 → 爆发 → 反杀 的情绪递进（可压缩在 60 秒内）。
6. 开头 3 秒必须「炸」：第一句台词就要有冲突或悬念。"""

OUTLINE_PROMPT = """题材：{theme}，风格：{style}，总时长约 {duration} 秒。

{synopsis_section}

{series_section}
先输出剧情大纲（节奏骨架），JSON 对象，不要其它文字：
{{
  "hook_first_3s": "开头3秒抓眼球的一句话或动作描述",
  "beats": ["羞辱", "反击", "爆发", "反杀"],
  "outline": "用3-6句话概括起承转合，点明核心爽点"
}}

{rag_block}"""

SCENES_PROMPT = """题材：{theme}，风格：{style}，总时长 {duration} 秒，每 5 秒一个分镜，共 {num_scenes} 个分镜。

{synopsis_section}

{series_section}
必须严格遵循以下大纲与钩子：
{outline_json}

输出一个 JSON 数组（仅此数组），每个元素字段：
- time: 如 "0-5s" 或 "0-5"；**第一个分镜建议 "0-3s" 对应开头炸裂 3 秒**，其后每格约 5 秒。
- scene: 画面描述（环境+动作，简练）
- camera: 镜头语言（如：俯视+慢推、特写、仰拍）
- dialogue: 台词（短！）
- emotion: 情绪
- role: 说话人角色，取值之一：主角、反派、女主、路人、旁白（用于分角色配音）

示例：
[
  {{"time": "0-3s", "scene": "斩仙台，乌云压顶", "camera": "俯视+慢推", "dialogue": "你也配成仙？", "emotion": "嘲讽", "role": "反派"}},
  {{"time": "3-8s", "scene": "主角抬头，眼神如刀", "camera": "特写", "dialogue": "今日，斩你成灰。", "emotion": "冰冷", "role": "主角"}}
]"""

# 兼容：单步旧版
LEGACY_PROMPT = """生成一个短视频剧本，题材：{theme}，风格：{style}。
{synopsis_section}

{series_section}
时长 {duration} 秒，每 5 秒一个分镜，共 {num_scenes} 个分镜。
每个分镜：scene, camera, dialogue（短）, emotion, role（主角/反派/女主/路人/旁白）。
只输出 JSON 数组。
{rag_block}"""

ONE_LINER_USER = """用户只给了一句话作为短视频创意：
「___LINE___」

风格：___STYLE___，目标总时长约 ___DURATION___ 秒，分镜约 ___NUM_SCENES___ 个（每约 5 秒一镜，**首镜建议 time 为 0-3s**）。

请只输出**一个 JSON 对象**（不要用 markdown 代码围栏），结构严格如下：
{{
  "script": "剧本梗概：用 2～4 小段讲清人物、核心冲突、反转与收束，适合竖屏短剧。",
  "scenes": [
    {{
      "time": "0-3s",
      "scene": "画面：环境+人物动作，简练",
      "camera": "镜头语言，如俯视+慢推、特写",
      "dialogue": "台词，尽量短",
      "emotion": "情绪",
      "role": "主角|反派|女主|路人|旁白",
      "image_prompt": "本镜文生图提示词：英文为主，含 cinematic lighting、景别与画面主体，可直接喂给 DALL·E/SD",
      "voice_text": "本镜配音全文：可与 dialogue 相同；无台词镜可写「……」或短旁白"
    }}
  ]
}}

硬性要求：scenes 长度与 num_scenes 一致或接近；每镜 image_prompt、voice_text 必须非空。"""


def _one_liner_prompt(line: str, style: str, duration: int, num_scenes: int) -> str:
    return (
        ONE_LINER_USER.replace("___LINE___", line)
        .replace("___STYLE___", style)
        .replace("___DURATION___", str(duration))
        .replace("___NUM_SCENES___", str(num_scenes))
    )


def expand_from_one_liner(
    line: str,
    style: str = "爽文",
    duration: int = 60,
) -> dict[str, Any]:
    """
    一句话扩写：返回剧本梗概 script + 分镜列表，每镜含 image_prompt、voice_text。
    """
    line = (line or "").strip()
    if not line:
        raise ValueError("一句话不能为空")
    duration = max(30, min(120, int(duration)))
    num_scenes = max(1, duration // 5)
    content = _invoke_llm(DIRECTOR_SYSTEM, _one_liner_prompt(line, style, duration, num_scenes))
    obj = _parse_json_object(content)
    script_text = str(obj.get("script") or "").strip()
    if not script_text:
        script_text = line
    raw_scenes = obj.get("scenes")
    if not isinstance(raw_scenes, list):
        raw_scenes = []

    scenes_out: list[dict[str, Any]] = []
    for i, s in enumerate(raw_scenes):
        if not isinstance(s, dict):
            continue
        norm = _normalize_scene(i, s)
        img_p = str(s.get("image_prompt") or "").strip()
        if not img_p:
            img_p = build_visual_prompt(norm)
        voice = str(s.get("voice_text") or "").strip() or str(norm.get("dialogue") or "").strip()
        if not voice:
            voice = "……"
        scenes_out.append(
            {
                **norm,
                "image_prompt": img_p[:4000],
                "voice_text": voice[:4096],
            }
        )

    if not scenes_out:
        theme = line[:24] if len(line) > 24 else line
        fb = build_fallback_draft_scenes(theme, style, duration, line)
        for i, n in enumerate(fb):
            vp = build_visual_prompt(n)
            d = str(n.get("dialogue") or "").strip() or "……"
            scenes_out.append({**n, "image_prompt": vp[:4000], "voice_text": d[:4096]})

    return {"script": script_text, "scenes": scenes_out}


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    return {}


def _parse_scenes_from_response(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    return []


def _normalize_scene(i: int, s: dict) -> dict[str, Any]:
    if not isinstance(s, dict):
        return {}
    t = s.get("time", f"{i*5}-{(i+1)*5}s")
    if isinstance(t, str) and "s" not in t.lower() and "-" in t:
        t = t + "s"
    return {
        "time": t,
        "scene": s.get("scene", ""),
        "camera": s.get("camera", ""),
        "dialogue": s.get("dialogue", ""),
        "emotion": s.get("emotion", ""),
        "role": s.get("role", "主角"),
    }


def _synopsis_section(synopsis: Optional[str]) -> str:
    s = (synopsis or "").strip()
    if s:
        return f"【故事简介】（必须完整融入情节与分镜，不可偏离）：\n{s}"
    return "【故事简介】用户未提供，请在题材与风格基础上自由发挥爽点剧情。"


def _series_section(series_id: Optional[str], episode: int) -> str:
    sid = (series_id or "").strip()
    if not sid:
        return ""
    return (
        f"【连续剧】series_id={sid}，当前为第 {episode} 集。\n"
        f"- 必须承接下方「历史剧情参考」，不得重复前文已用过的核心桥段与台词。\n"
        f"- 本集冲突/对立须明显升级（更强敌、更大危机、更狠反转）。\n"
        f"- 结尾留新钩子，便于下一集接续。\n"
    )


def _save_script_memory(
    theme: str,
    style: str,
    series_id: Optional[str],
    episode: int,
    scenes: list[dict[str, Any]],
    outline_obj: Optional[dict[str, Any]],
) -> None:
    hook = ""
    outline_txt = ""
    if outline_obj:
        hook = str(outline_obj.get("hook_first_3s", ""))
        outline_txt = str(outline_obj.get("outline", ""))
    summ = " | ".join([(str(x.get("scene") or ""))[:60] for x in scenes[:8]])
    if not outline_txt and scenes:
        outline_txt = " ".join((str(s.get("dialogue") or "")) for s in scenes[:5])[:300]
    world_state = f"第{episode}集收束：冲突推进；{outline_txt[:180]}"
    sid = (series_id or "").strip()
    if sid:
        rag_service.save_series_episode(
            sid,
            episode,
            theme,
            style,
            summ,
            world_state,
            hook=hook,
            outline=outline_txt,
        )
    else:
        rag_service.save_episode(theme, style, hook, outline_txt, summ)


def normalize_scenes_list(raw: list) -> list[dict[str, Any]]:
    """校验并规范化用户编辑后的分镜列表。"""
    if not raw or not isinstance(raw, list):
        raise ValueError("剧本必须是非空 JSON 数组")
    out = [_normalize_scene(i, s) for i, s in enumerate(raw) if isinstance(s, dict)]
    if not out:
        raise ValueError("剧本至少需要 1 个分镜对象")
    return out


def _is_openai_rate_limit(exc: BaseException) -> bool:
    try:
        import openai

        if isinstance(exc, openai.RateLimitError):
            return True
        if isinstance(exc, openai.APIStatusError) and getattr(exc, "status_code", None) == 429:
            return True
    except ImportError:
        pass
    low = str(exc).lower()
    return "429" in low and ("rate" in low or "limit" in low)


def _local_llm_configured() -> bool:
    base = (getattr(settings, "local_llm_base_url", None) or "").strip()
    model = (getattr(settings, "local_llm_model", None) or "").strip()
    return bool(base and model)


def _invoke_local_llm(system: str, user: str) -> str:
    if not _local_llm_configured():
        raise RuntimeError("未配置 LOCAL_LLM_BASE_URL / LOCAL_LLM_MODEL，无法用本地模型生成剧本")
    base = settings.local_llm_base_url.strip().rstrip("/")
    timeout = float(getattr(settings, "local_llm_timeout_sec", 120.0) or 120.0)
    llm = ChatOpenAI(
        model=settings.local_llm_model.strip(),
        api_key=(settings.local_llm_api_key or "ollama").strip() or "ollama",
        base_url=base,
        temperature=0.85,
        timeout=timeout,
        max_retries=0,
    )
    msg = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return msg.content if hasattr(msg, "content") else str(msg)


def _invoke_openai_llm_with_429_backoff(system: str, user: str) -> str:
    max_r = max(1, int(getattr(settings, "script_openai_429_max_retries", 4)))
    base_delay = float(getattr(settings, "script_openai_429_base_delay_sec", 2.0))

    def _call(api_key: str) -> str:
        last: BaseException | None = None
        for attempt in range(max_r):
            try:
                llm = ChatOpenAI(
                    model=getattr(settings, "openai_script_model", "gpt-4o"),
                    api_key=api_key,
                    temperature=0.85,
                    timeout=120.0,
                    max_retries=0,
                )
                msg = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
                return msg.content if hasattr(msg, "content") else str(msg)
            except Exception as e:
                last = e
                if _is_openai_rate_limit(e) and attempt < max_r - 1:
                    delay = base_delay * (2**attempt) + random.uniform(0, 0.75)
                    time.sleep(delay)
                    continue
                raise
        raise last if last else RuntimeError("OpenAI 调用失败")

    return run_with_key_rotation(_call, what="剧本生成")


def _invoke_llm(system: str, user: str) -> str:
    mode = (getattr(settings, "script_llm_mode", "openai") or "openai").lower().strip()
    if mode == "local":
        return _invoke_local_llm(system, user)
    if mode == "openai_fallback_local":
        try:
            return _invoke_openai_llm_with_429_backoff(system, user)
        except Exception:
            if _local_llm_configured():
                return _invoke_local_llm(system, user)
            raise
    return _invoke_openai_llm_with_429_backoff(system, user)


def _fallback_dialogue_line(
    index: int,
    theme: str,
    synopsis: Optional[str],
) -> tuple[str, str]:
    """
    兜底分镜的真实短台词（非括号说明），与 role 配对。
    返回 (dialogue, role)。
    """
    syn = (synopsis or "").strip()
    th = (theme or "复仇").strip()[:12]

    # 首镜：尽量从简介抽钩子，否则用主题向狠话
    if index == 0:
        if len(syn) >= 4:
            hook = syn[:16].replace("\n", " ").strip()
            line = f"{hook}，今日见分晓。"
            if len(line) > 28:
                line = f"{hook[:10]}……今日见分晓。"
            return (line, "反派")
        return (f"就凭你，也配谈{th}？", "反派")

    # 轮换：爽文短句，单句≤约14字，可直接 TTS
    pairs = [
        ("主角", "我若不死，便是你们的劫。"),
        ("反派", "给我拿下！"),
        ("主角", "让开。"),
        ("路人", "那边……出事了！"),
        ("主角", "该清算了。"),
        ("反派", "狂妄！"),
        ("主角", "轮到你了。"),
        ("旁白", "风雷骤起。"),
    ]
    role, line = pairs[(index - 1) % len(pairs)]
    return (line, role)


def build_fallback_draft_scenes(
    theme: str,
    style: str,
    duration: int,
    synopsis: Optional[str] = None,
) -> list[dict[str, Any]]:
    """大模型或网络失败时的可编辑占位分镜（与正常剧本字段一致）。"""
    num = max(1, duration // 5)
    syn = (synopsis or "").strip()
    raw: list[dict[str, Any]] = []
    t_end = 0
    for i in range(num):
        if i == 0:
            t0, t1 = 0, min(3, duration)
            time_s = f"{t0}-{t1}s"
            t_end = t1
        else:
            t0 = t_end
            t1 = min(t0 + 5, duration)
            if t1 <= t0:
                t1 = min(t0 + 1, duration)
            time_s = f"{t0}-{t1}s"
            t_end = t1
        scene_hint = f"【{theme}】{style}向，第{i + 1}镜"
        if syn and i == 0:
            scene_hint += f"；参考简介：{syn[:120]}"
        elif i == 0:
            scene_hint += "；开场需有冲突或悬念"
        dlg, rrole = _fallback_dialogue_line(i, theme, syn if i == 0 else None)
        raw.append(
            {
                "time": time_s,
                "scene": scene_hint,
                "camera": "俯视+慢推" if i == 0 else ("特写" if i % 2 else "中景跟拍"),
                "dialogue": dlg,
                "emotion": ["压抑", "反击", "爆发", "冷笑", "杀意"][min(i, 4)],
                "role": rrole,
            }
        )
    return normalize_scenes_list(raw)


def generate_script(
    theme: str,
    style: str,
    duration: int,
    rag_context: Optional[str] = None,
    synopsis: Optional[str] = None,
    series_id: Optional[str] = None,
    episode: int = 1,
) -> list[dict[str, Any]]:
    """
    生成短剧分镜。两步：大纲 + 分镜（可配置关闭为单步）。
    synopsis：用户故事简介，会强约束剧情走向。
    series_id + episode：连续剧模式，从 Chroma 拉历史并写回本集摘要。
    """
    num_scenes = max(1, duration // 5)
    syn_sec = _synopsis_section(synopsis)
    ser_sec = _series_section(series_id, episode)
    ep = max(1, int(episode))
    rag_block = ""
    if rag_context:
        rag_block = "\n\n" + rag_context
    elif getattr(settings, "rag_enabled", True):
        sid = (series_id or "").strip()
        if sid:
            ctx = rag_service.get_story_context(sid, ep)
            if ctx:
                rag_block = "\n\n" + ctx
        else:
            rag_block = "\n\n" + rag_service.query_context(theme, style)

    if not getattr(settings, "script_two_step", True):
        content = _invoke_llm(
            DIRECTOR_SYSTEM,
            LEGACY_PROMPT.format(
                theme=theme,
                style=style,
                duration=duration,
                num_scenes=num_scenes,
                synopsis_section=syn_sec,
                series_section=ser_sec,
                rag_block=rag_block,
            ),
        )
        raw = _parse_scenes_from_response(content)
        scenes = [_normalize_scene(i, s) for i, s in enumerate(raw) if isinstance(s, dict)]
        if not scenes:
            return build_fallback_draft_scenes(theme, style, duration, synopsis)
        try:
            _save_script_memory(theme, style, series_id, ep, scenes, None)
        except Exception:
            pass
        return scenes

    outline_raw = _invoke_llm(
        DIRECTOR_SYSTEM,
        OUTLINE_PROMPT.format(
            theme=theme,
            style=style,
            duration=duration,
            synopsis_section=syn_sec,
            series_section=ser_sec,
            rag_block=rag_block,
        ),
    )
    outline_obj = _parse_json_object(outline_raw)
    if not outline_obj:
        outline_obj = {
            "hook_first_3s": f"{theme}开场即冲突",
            "beats": ["羞辱", "反击", "爆发", "反杀"],
            "outline": f"{theme}，{style}向短视频爽点。",
        }
    outline_json = json.dumps(outline_obj, ensure_ascii=False)

    content = _invoke_llm(
        DIRECTOR_SYSTEM,
        SCENES_PROMPT.format(
            theme=theme,
            style=style,
            duration=duration,
            num_scenes=num_scenes,
            synopsis_section=syn_sec,
            series_section=ser_sec,
            outline_json=outline_json,
        ),
    )
    raw = _parse_scenes_from_response(content)
    scenes = [_normalize_scene(i, s) for i, s in enumerate(raw) if isinstance(s, dict)]
    if not scenes:
        return build_fallback_draft_scenes(theme, style, duration, synopsis)

    try:
        _save_script_memory(theme, style, series_id, ep, scenes, outline_obj)
    except Exception:
        pass

    return scenes
