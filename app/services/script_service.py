"""剧本：可选两步生成（大纲 → 分镜+镜头语言），接 RAG 参考。"""
import json
import math
import random
import re
import time
from typing import Any, Optional, Union

import httpx
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings
from app.services import rag_service
from app.services.model_debug_io import print_chat_model_io
from app.services.openai_keys import run_with_key_rotation
from app.services.visual_prompt import build_visual_prompt

_ERR_LOCAL_SCENES_PARSE = (
    "未能解析出分镜。请确认 Ollama 已启动、模型已 pull；"
    "pip install -r requirements.txt（含 json-repair）；"
    "或换用更大模型（如 qwen2.5:3b）。"
)


def _num_scenes_for_duration(duration: int) -> int:
    """按总时长估算分镜条数（约每 5 秒一镜）。"""
    return max(1, int(duration) // 5)


def _ollama_root_from_openai_base_url(base: str) -> Optional[str]:
    """http://host:11434/v1 -> http://host:11434"""
    b = (base or "").strip().rstrip("/")
    if not b:
        return None
    if b.lower().endswith("/v1"):
        return b[:-3].rstrip("/") or None
    return b


def _should_try_ollama_native_chat(base: str) -> bool:
    bl = (base or "").lower()
    return "11434" in bl or ("/v1" in bl and "ollama" in bl)


def _invoke_ollama_native_chat(system: str, user: str, ollama_root: str) -> str:
    """Ollama 原生 /api/chat，format=json 由服务端约束为合法 JSON。"""
    url = f"{ollama_root.rstrip('/')}/api/chat"
    model = settings.local_llm_model.strip()
    temp = float(getattr(settings, "local_llm_script_temperature", 0.35) or 0.35)
    timeout = float(getattr(settings, "local_llm_timeout_sec", 120.0) or 120.0)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": temp},
    }
    if getattr(settings, "local_llm_json_response", True):
        payload["format"] = "json"
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    msg = data.get("message") or {}
    content: Any = msg.get("content")
    if content is None:
        raise RuntimeError("Ollama /api/chat 返回无 message.content")
    if isinstance(content, (dict, list)):
        out = json.dumps(content, ensure_ascii=False)
    else:
        out = str(content)
    print_chat_model_io(
        f"Ollama原生 /api/chat model={model}",
        system,
        user,
        out,
    )
    return out

DIRECTOR_SYSTEM = """你是竖屏短剧/短视频分镜导演，擅长修仙复仇、爽点密集的抖音向内容。

【节奏】约每 5 秒必须有信息增量：冲突、反转、打脸或情绪跃迁之一；禁止流水账与长篇说明。
【台词】单句尽量不超过 12 字；口语、狠、好记；禁止议论文式长句。
【情绪】外放鲜明：愤怒、冷笑、绝望、杀意、威压等；少用「平静」「一般」。
【镜头】每镜 camera 必须写清景别或运动（特写、仰拍、俯拍、跟拍、推轨、闪切、慢动作等），便于拍摄与文生图。
【结构】在总时长内完成：受压 → 反击 → 爆发 → 翻盘/收束；前 3 秒必须强钩子（动作或台词撞击）。
【输出】严格按用户要求的 JSON 结构输出；键名用英文双引号；不要 markdown 代码围栏；不要「好的」「以下是」等套话。"""

OUTLINE_PROMPT = """题材：{theme}，风格：{style}，总时长约 {duration} 秒（后续分镜会铺满 0～{duration} 秒，大纲节奏需对齐）。

{synopsis_section}

{series_section}
本步**只输出剧情大纲**，一个 JSON 对象，不要数组、不要分镜、不要其它说明文字：
{{
  "hook_first_3s": "前 3 秒内抓眼球的动作或台词钩子，一句话",
  "beats": ["阶段1情绪名", "阶段2", "阶段3", "阶段4"],
  "outline": "用 3～6 句概括起承转合与核心爽点，便于下一步拆成约每 5 秒一镜"
}}

beats 可随题材微调，但必须体现递进（如压迫→质疑→爆发→翻盘）。

{rag_block}"""

SCENES_PROMPT = """题材：{theme}，风格：{style}，总时长 {duration} 秒；需 **{num_scenes} 条分镜**，约每 5 秒一镜，铺满 0～{duration} 秒。

{synopsis_section}

{series_section}
必须落实下面的大纲与钩子（可细化镜头，不可跑题）：
{outline_json}

只输出 **一个 JSON 数组**（不要最外层对象、不要 markdown），**元素个数必须等于 {num_scenes}**。

每条分镜字段（缺一不可）：
- time: 字符串，格式 "起始-结束s"，如 "8-13s"。**首镜建议 "0-3s"**；从第二镜起每段跨度 **约 4～6 秒**。**禁止单镜跨度超过 8 秒**（禁止把高潮写成 "28-60s" 一条，必须拆成多镜）。相邻镜时间要衔接（上一镜结束秒 = 下一镜开始秒）。最后一镜的结束时间 ≤ {duration}。
- scene: **单个字符串**，本镜可见的画面（环境+人物动作），一句到两句；禁止数组、禁止把台词塞进 scene。
- camera: 本镜镜头语言（与画面匹配）。
- dialogue: 本镜台词，短；无台词可写 "……" 或极短气声。
- emotion: 本镜情绪关键词。
- role: 说话人，只能是：主角、反派、女主、路人、旁白（无台词时 role 可标旁白或主角视剧情而定）。

反例（禁止）：把多镜合并成一条；scene 写成数组；time 一条跨 20 秒以上；输出少于 {num_scenes} 条。

正例片段：
[
  {{"time": "0-3s", "scene": "斩仙台，乌云压顶", "camera": "俯视+慢推", "dialogue": "你也配成仙？", "emotion": "嘲讽", "role": "反派"}},
  {{"time": "3-8s", "scene": "主角抬头，眼神如刀", "camera": "特写", "dialogue": "今日，斩你成灰。", "emotion": "冰冷", "role": "主角"}}
]"""

# 兼容：单步旧版
LEGACY_PROMPT = """为竖屏短视频写分镜 JSON。题材：{theme}，风格：{style}。
{synopsis_section}

{series_section}
总时长 {duration} 秒；需要 **{num_scenes} 条**分镜对象，**只输出 JSON 数组**，不要其它文字。

每条含：time, scene, camera, dialogue, emotion, role（主角/反派/女主/路人/旁白）。
- time：首镜建议 0-3s；之后每镜约 4～6 秒一格，**禁止单镜超过 8 秒**；0～{duration} 秒连续铺满；最后一镜结束 ≤ {duration}。
- scene：必须是字符串，一句画面；禁止数组、禁止英文占位词。
- dialogue：短句口语。

{rag_block}"""

ONE_LINER_USER = """用户只给了一句话作为短视频创意：
「___LINE___」

风格：___STYLE___，目标总时长约 ___DURATION___ 秒；请输出约 ___NUM_SCENES___ 条分镜（约每 5 秒一镜，**首镜 time 用 0-3s**）。

请只输出**一个 JSON 对象**（不要用 markdown 代码围栏），结构如下：
{{
  "script": "2～4 句梗概：人物、冲突、反转、收束，竖屏短剧向。",
  "scenes": [
    {{
      "time": "0-3s",
      "scene": "本镜画面：环境+动作，简练",
      "camera": "镜头语言",
      "dialogue": "短台词",
      "emotion": "情绪",
      "role": "主角|反派|女主|路人|旁白",
      "image_prompt": "英文文生图提示：主体+景别+光影，含 cinematic lighting, vertical 9:16, 与 scene 一致",
      "voice_text": "本镜配音全文；可与 dialogue 相同；无台词可用「……」"
    }}
  ]
}}

硬性要求：
- scenes 条数与 ___NUM_SCENES___ 一致或尽量接近。
- 每镜 time：**除首镜外每段约 4～6 秒**，禁止单镜跨度超过 8 秒；时间轴 0～___DURATION___ 秒衔接铺满。
- scene 为字符串；image_prompt、voice_text 均非空；禁止把 scene 写成数组。"""


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
    *,
    qwen_only: bool = False,
) -> dict[str, Any]:
    """
    一句话扩写：返回剧本梗概 script + 分镜列表，每镜含 image_prompt、voice_text。
    qwen_only=True 时仅走兜底 Qwen（流水线二次重试）。
    """
    line = (line or "").strip()
    if not line:
        raise ValueError("一句话不能为空")
    duration = max(30, min(120, int(duration)))
    num_scenes = _num_scenes_for_duration(duration)
    prompt = _one_liner_prompt(line, style, duration, num_scenes)
    content = _invoke_script_llm(DIRECTOR_SYSTEM, prompt, qwen_only=qwen_only)
    obj = _parse_json_object(content)
    script_text = str(obj.get("script") or "").strip()
    if not script_text:
        script_text = line
    raw_scenes = obj.get("scenes")
    if not isinstance(raw_scenes, list):
        raw_scenes = []

    norms: list[dict[str, Any]] = []
    raws: list[dict[str, Any]] = []
    for i, s in enumerate(raw_scenes):
        if not isinstance(s, dict):
            continue
        norms.append(_normalize_scene(i, s))
        raws.append(s)
    norms = _maybe_expand_underfilled_scenes(norms, num_scenes, duration)
    norms = _split_oversized_time_scenes(norms, duration=duration)

    scenes_out: list[dict[str, Any]] = []
    for j, norm in enumerate(norms):
        s = raws[j] if j < len(raws) else {}
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

    if not scenes_out and not qwen_only and qwen_configured() and not script_llm_mode_is_local():
        return expand_from_one_liner(line, style, duration, qwen_only=True)
    if not scenes_out:
        raise RuntimeError(
            "一句话扩写未得到任何分镜，请检查 LOCAL_LLM_* 与模型 JSON 输出"
            if script_llm_mode_is_local()
            else "一句话扩写未得到任何分镜，请检查模型输出或 QWEN_* / 主模型配置后重试"
        )

    return {"script": script_text, "scenes": scenes_out}


def _llm_response_text(raw: Any) -> str:
    """去掉 BOM、首尾空白；模型偶发返回 list/tuple 消息片段。"""
    if raw is None:
        return ""
    if isinstance(raw, (list, tuple)):
        parts: list[str] = []
        for x in raw:
            if isinstance(x, dict) and x.get("type") == "text":
                parts.append(str(x.get("text", "")))
            else:
                parts.append(str(x))
        raw = "".join(parts)
    t = str(raw).replace("\ufeff", "").strip()
    return t


def _message_content_as_str(msg: Any) -> str:
    raw = msg.content if hasattr(msg, "content") else msg
    return _llm_response_text(raw)


def _unwrap_markdown_json_fence(text: str) -> str:
    if "```" not in text:
        return text
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    return m.group(1).strip() if m else text


def _balance_json_object(s: str) -> Optional[str]:
    """从首个 { 起按 JSON 双引号字符串规则匹配到成对的 }。"""
    idx = s.find("{")
    if idx == -1:
        return None
    depth = 0
    i = idx
    n = len(s)
    in_string = False
    escape = False
    while i < n:
        c = s[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[idx : i + 1]
        i += 1
    return None


def _balance_json_array(s: str) -> Optional[str]:
    """从首个 [ 起匹配到成对的 ]（忽略字符串内的括号）。"""
    idx = s.find("[")
    if idx == -1:
        return None
    depth = 0
    i = idx
    n = len(s)
    in_string = False
    escape = False
    while i < n:
        c = s[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return s[idx : i + 1]
        i += 1
    return None


def _normalize_json_string_quotes(s: str) -> str:
    """弯引号、全角引号改为 ASCII，减少小模型 JSON 解析失败。"""
    for old, new in (
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2018", "'"),
        ("\u2019", "'"),
        ("\uff02", '"'),
    ):
        s = s.replace(old, new)
    return s


def _wrap_loose_scene_objects(s: str) -> list[str]:
    """模型输出 {...},{...} 或单个 {...} 而无 [] 时，补成数组字符串。"""
    t = s.strip()
    if not t.startswith("{") or t.startswith("["):
        return []
    if "},{" in t:
        return ["[" + t + "]"]
    if t.endswith("}") and t.count("{") == 1:
        return ["[" + t + "]"]
    return []


def _try_json_loads(s: str) -> Union[dict, list, None]:
    s = _normalize_json_string_quotes((s or "").strip())
    if not s:
        return None
    try:
        v = json.loads(s)
        return v
    except json.JSONDecodeError:
        pass
    try:
        fixed = re.sub(r",\s*([}\]])", r"\1", s)
        if fixed != s:
            v = json.loads(fixed)
            return v
    except json.JSONDecodeError:
        pass
    try:
        from json_repair import repair_json

        try:
            obj = repair_json(s, return_objects=True)
        except TypeError:
            repaired = repair_json(s)
            obj = json.loads(repaired) if isinstance(repaired, str) else repaired
        if isinstance(obj, (dict, list)):
            return obj
    except ImportError:
        pass
    except Exception:
        pass
    return None


def _strip_json_dict_keys(d: dict) -> dict[str, Any]:
    """小模型常在键名前输出空格；去掉空键名（如 \" \" 误作键）。"""
    out: dict[str, Any] = {}
    for k, v in d.items():
        ks = str(k).strip()
        if ks:
            out[ks] = v
    return out


def _scene_field_is_placeholder(s: str) -> bool:
    """scene 字段仅为镜头/占位词，无实际画面描写。"""
    s = (s or "").strip()
    if not s:
        return True
    if s in ("特写,对话", "特写，对话", "对话", "特写", "对白", "近景", "远景", "俯拍", "仰拍"):
        return True
    if re.fullmatch(r"(特写|近景|远景|俯拍|仰拍|跟拍|慢推)([,，]\s*(对话|对白))?", s):
        return True
    if re.fullmatch(r"(对话|对白|镜头|分镜|scene|camera)", s, re.I):
        return True
    return False


def _hook_mixed_to_scene_list(v: dict) -> Optional[list[dict[str, Any]]]:
    """
    分镜步误返回「大纲 hook + 单镜字段」混在一个对象里时，收成一条标准分镜。
    例：hook_first_3s + scene=\"特写,对话\" + dialogue。
    """
    hook = str(v.get("hook_first_3s") or "").strip()
    if not hook:
        return None
    if str(v.get("outline") or "").strip():
        return None
    beats = v.get("beats")
    if isinstance(beats, list) and len(beats) > 0:
        return None
    if isinstance(v.get("scenes"), list) and len(v.get("scenes") or []) > 0:
        return None
    dialogue = str(v.get("dialogue") or "").strip()
    scene_s = str(v.get("scene") or "").strip()
    if not dialogue and not scene_s:
        return None
    if scene_s and not _scene_field_is_placeholder(scene_s):
        return None
    camera = str(v.get("camera") or "").strip()
    time_v = str(v.get("time") or "").strip() or "0-3s"
    emotion = str(v.get("emotion") or "").strip()
    role = str(v.get("role") or "").strip() or "主角"
    visual = hook
    m = re.search(r"场景[：:]\s*(.+)$", hook)
    if m:
        visual = m.group(1).strip()
    if "特写" in scene_s and not camera:
        camera = "特写"
    elif re.match(r"^(近景|远景|俯拍|仰拍|跟拍)", scene_s):
        mm = re.match(r"^(近景|远景|俯拍|仰拍|跟拍)", scene_s)
        if mm:
            camera = mm.group(1)
    return [
        {
            "time": time_v,
            "scene": visual or hook,
            "camera": camera,
            "dialogue": dialogue,
            "emotion": emotion,
            "role": role,
        }
    ]


def _outline_json_usable(d: dict) -> bool:
    """大纲 JSON 是否像样（排除分镜形状、含台词等误输出）。"""
    if not isinstance(d, dict) or not d:
        return False
    if isinstance(d.get("scene"), list):
        return False
    if str(d.get("dialogue") or "").strip():
        return False
    if str(d.get("outline") or "").strip() or str(d.get("hook_first_3s") or "").strip():
        return True
    beats = d.get("beats")
    return isinstance(beats, list) and len(beats) > 0


def _parse_json_object(text: str) -> dict[str, Any]:
    text = _llm_response_text(text)
    text = _unwrap_markdown_json_fence(text)
    candidates: list[str] = []
    bal = _balance_json_object(text)
    if bal:
        candidates.append(bal)
    candidates.append(text)
    a, b = text.find("{"), text.rfind("}") + 1
    if a != -1 and b > a:
        sub = text[a:b]
        if sub not in candidates:
            candidates.append(sub)
    for candidate in candidates:
        if not candidate.strip():
            continue
        v = _try_json_loads(candidate)
        if isinstance(v, dict):
            return _strip_json_dict_keys(v)
        if isinstance(v, list) and len(v) == 1 and isinstance(v[0], dict):
            return _strip_json_dict_keys(v[0])
    return {}


def _try_expand_scene_string_list_top_object(v: dict) -> Optional[list[dict[str, Any]]]:
    """
    处理 {\"scene\": [\"场景1\", \"场景2\"]}：仅多段画面文案、无其它有效元数据时拆成多条分镜。
    """
    scene_val = v.get("scene")
    if not isinstance(scene_val, list):
        return None
    strs: list[str] = []
    for x in scene_val:
        if isinstance(x, dict):
            return None
        if x is None:
            continue
        t = str(x).strip()
        if t:
            strs.append(t)
    if len(strs) < 2:
        return None
    meta_keys = frozenset({"time", "camera", "dialogue", "emotion", "role"})
    other = {k: val for k, val in v.items() if k != "scene"}
    meta_filled = any(
        k in meta_keys and val not in (None, "", [], {})
        for k, val in other.items()
    )
    if meta_filled:
        return None
    return [{"scene": s} for s in strs]


def _scene_list_singleton_to_one_row(v: dict) -> Optional[list[dict[str, Any]]]:
    """{\"scene\": [\"一段画面\"]} 且数组里只有一条有效文案时，收成单条分镜对象。"""
    sv = v.get("scene")
    if not isinstance(sv, list):
        return None
    strs = [str(x).strip() for x in sv if x is not None and str(x).strip()]
    if len(strs) != 1:
        return None
    row = {**v, "scene": strs[0]}
    return [_strip_json_dict_keys(row)]


def _parse_scenes_from_response(text: str) -> list[dict[str, Any]]:
    text = _llm_response_text(text)
    text = _unwrap_markdown_json_fence(text)
    candidates: list[str] = []
    obj_bal = _balance_json_object(text)
    if obj_bal:
        candidates.append(obj_bal)
    arr_bal = _balance_json_array(text)
    if arr_bal:
        candidates.append(arr_bal)
    candidates.append(text)
    a, b = text.find("["), text.rfind("]") + 1
    if a != -1 and b > a:
        sub = text[a:b]
        if sub not in candidates:
            candidates.append(sub)
    expanded: list[str] = []
    for c in candidates:
        if not (c or "").strip():
            continue
        expanded.append(c)
        expanded.extend(_wrap_loose_scene_objects(c))

    for candidate in expanded:
        if not candidate.strip():
            continue
        v = _try_json_loads(candidate)
        if isinstance(v, list):
            out = [
                _strip_json_dict_keys(item)
                for item in v
                if isinstance(item, dict)
            ]
            if out:
                return out
            continue
        if isinstance(v, dict):
            v = _strip_json_dict_keys(v)
            from_scene_list = _try_expand_scene_string_list_top_object(v)
            if from_scene_list is not None:
                return from_scene_list
            one_from_list = _scene_list_singleton_to_one_row(v)
            if one_from_list is not None:
                return one_from_list
            hook_scenes = _hook_mixed_to_scene_list(v)
            if hook_scenes is not None:
                return hook_scenes
            sc = v.get("scenes")
            if isinstance(sc, list):
                return [_strip_json_dict_keys(x) if isinstance(x, dict) else x for x in sc]
            script = v.get("script")
            if isinstance(script, list):
                return [_strip_json_dict_keys(x) if isinstance(x, dict) else x for x in script]
            if any(
                k in v
                for k in ("time", "scene", "dialogue", "camera", "role", "emotion")
            ):
                return [v]
    return []


_FIELD_NOISE_LOWER = frozenset(
    {
        "dialogue",
        "emotion",
        "role",
        "camera",
        "time",
        "scene",
        "image_prompt",
        "voice_text",
    }
)
_CAMERA_SUBSTR = (
    "特写",
    "近景",
    "远景",
    "俯拍",
    "仰拍",
    "跟拍",
    "慢推",
    "俯视",
    "仰视",
    "推拉",
    "摇镜",
    "航拍",
    "推镜",
    "拉镜",
)


def _repair_scene_scene_field_as_list(s: dict) -> dict:
    """小模型常把 time/镜头/台词压进 scene 数组，拆回各字段。"""
    out = dict(s)
    raw = out.get("scene")
    if not isinstance(raw, list):
        return out
    if len(raw) == 0:
        out["scene"] = ""
        return out
    items: list[str] = []
    for x in raw:
        if x is None:
            continue
        t = str(x).strip()
        if t:
            items.append(t)
    items = [x for x in items if x.lower() not in _FIELD_NOISE_LOWER]
    times_extra: list[str] = []
    kept: list[str] = []
    for x in items:
        if re.fullmatch(r"\d+-\d+s?", x, re.IGNORECASE):
            times_extra.append(x)
        else:
            kept.append(x)
    camera_guess = ""
    narrative: list[str] = []
    for x in kept:
        if (
            not camera_guess
            and len(x) <= 20
            and any(h in x for h in _CAMERA_SUBSTR)
        ):
            camera_guess = x
            continue
        narrative.append(x)
    dialogue_guess = ""
    if narrative:
        last = narrative[-1]
        core = last.rstrip("。！？…")
        inner_periods = core.count("。") + core.count("！") + core.count("？")
        if len(last) <= 36 and inner_periods == 0 and (
            last.count("，") <= 1 or len(last) <= 16
        ):
            dialogue_guess = last
            narrative = narrative[:-1]
    if not str(out.get("camera") or "").strip() and camera_guess:
        out["camera"] = camera_guess
    if not str(out.get("dialogue") or "").strip() and dialogue_guess:
        out["dialogue"] = dialogue_guess
    out["scene"] = (
        " ".join(narrative)
        if narrative
        else (" ".join(kept) if kept else "")
    )
    if times_extra and not str(out.get("time") or "").strip():
        out["time"] = times_extra[0]
    return out


def _coerce_scene_value_to_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, list):
        fixed = _repair_scene_scene_field_as_list({"scene": val})
        return str(fixed.get("scene") or "").strip()
    return str(val).strip()


def _maybe_expand_underfilled_scenes(
    scenes: list[dict[str, Any]],
    num_scenes: int,
    duration: int,
) -> list[dict[str, Any]]:
    """只返回 1 条时按句拆分 scene，尽力接近 num_scenes（不超过可拆句段数）。"""
    duration = max(5, int(duration))
    num_scenes = max(1, int(num_scenes))
    if num_scenes <= 1 or len(scenes) >= num_scenes or not scenes:
        return scenes
    s0 = scenes[0]
    text = str(s0.get("scene") or "").strip()
    if len(text) < 8:
        return scenes
    chunks = re.split(r"(?<=[。！？])\s*", text)
    chunks = [c.strip() for c in chunks if len(c.strip()) > 3]
    if len(chunks) < 2:
        chunks = [c.strip() for c in re.split(r"[；;]\s*", text) if len(c.strip()) > 3]
    if len(chunks) < 2:
        return scenes
    m = min(len(chunks), num_scenes)
    bounds = [int(round(duration * j / m)) for j in range(m + 1)]
    bounds[-1] = duration
    for j in range(m):
        if bounds[j + 1] <= bounds[j]:
            bounds[j + 1] = min(duration, bounds[j] + 1)
    out: list[dict[str, Any]] = []
    orig_dialogue = str(s0.get("dialogue") or "").strip()
    for i in range(m):
        row = dict(s0)
        row["scene"] = chunks[i]
        row["time"] = f"{bounds[i]}-{bounds[i + 1]}s"
        if i < m - 1:
            row["dialogue"] = ""
        elif orig_dialogue:
            row["dialogue"] = orig_dialogue
        out.append(row)
    return out


def _parse_time_span_str(t: str) -> Optional[tuple[int, int]]:
    m = re.match(
        r"^\s*(\d+)\s*-\s*(\d+)\s*s?\s*$",
        str(t or "").strip(),
        re.IGNORECASE,
    )
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if b <= a:
        return None
    return a, b


def _split_oversized_time_scenes(
    scenes: list[dict[str, Any]],
    *,
    duration: int,
    max_span_sec: int = 8,
    target_seg_sec: int = 5,
) -> list[dict[str, Any]]:
    """单镜 time 跨度过长时按约 target_seg_sec 秒拆成多条，并按逗号切分 scene 文案。"""
    cap = max(1, int(duration))
    out: list[dict[str, Any]] = []
    for row in scenes:
        if not isinstance(row, dict):
            continue
        span_pair = _parse_time_span_str(str(row.get("time") or ""))
        if span_pair is None:
            out.append(row)
            continue
        a, b = span_pair
        b = min(b, cap)
        if b <= a:
            out.append(row)
            continue
        span = b - a
        if span <= max_span_sec:
            out.append(row)
            continue
        n = max(2, int(math.ceil(span / target_seg_sec)))
        bounds = [a + int(round(span * j / n)) for j in range(n + 1)]
        bounds[0] = a
        bounds[-1] = b
        for j in range(n):
            if bounds[j + 1] <= bounds[j]:
                bounds[j + 1] = min(cap, bounds[j] + 1)
        text = str(row.get("scene") or "").strip()
        parts = [p.strip() for p in re.split(r"[，,、]+", text) if p.strip()]
        if not parts:
            parts = [text]
        while len(parts) < n:
            parts.append(parts[-1])
        while len(parts) > n:
            parts[-2] = parts[-2] + "，" + parts[-1]
            parts.pop()
        dlg = str(row.get("dialogue") or "").strip()
        for i in range(n):
            sub = dict(row)
            sub["time"] = f"{bounds[i]}-{bounds[i + 1]}s"
            sub["scene"] = parts[i]
            sub["dialogue"] = dlg if i == n - 1 else ""
            out.append(sub)
    return out


def _normalize_scene(i: int, s: dict) -> dict[str, Any]:
    if not isinstance(s, dict):
        return {}
    s = _repair_scene_scene_field_as_list(s)
    t = s.get("time", f"{i*5}-{(i+1)*5}s")
    if isinstance(t, str) and "s" not in t.lower() and "-" in t:
        t = t + "s"
    scene_str = _coerce_scene_value_to_str(s.get("scene"))
    dlg = str(s.get("dialogue") or "").strip()
    if not scene_str and dlg:
        scene_str = f"情绪戏：{dlg[:220]}"
    return {
        "time": t,
        "scene": scene_str,
        "camera": str(s.get("camera") or "").strip(),
        "dialogue": dlg,
        "emotion": str(s.get("emotion") or "").strip(),
        "role": str(s.get("role") or "").strip() or "主角",
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
    # 本地端点多为小模型，统一收紧 JSON 输出（含 openai_fallback_local 回退到本地时）
    system = (
        f"{system}\n\n【输出】只输出纯 JSON，不要用 markdown 代码块，不要「好的」等开场白。"
        "键名与字符串必须用英文双引号。"
    )
    user = (
        f"{user}\n\n【再次强调】分镜须为合法 JSON：优先输出数组 [...] ，每项含 "
        "time、scene、camera、dialogue、emotion、role；若只能输出对象，则用 "
        '{{"scenes":[...]}} 把数组放在 scenes 字段。'
        " scene 必须是字符串，不得为数组；条数与 num_scenes 一致；"
        "除首镜可 0-3s 外每镜 time 跨度约 4～6 秒，禁止单镜超过 8 秒。"
    )
    base = settings.local_llm_base_url.strip().rstrip("/")
    timeout = float(getattr(settings, "local_llm_timeout_sec", 120.0) or 120.0)
    temp = float(getattr(settings, "local_llm_script_temperature", 0.35) or 0.35)
    openai_base = settings.local_llm_base_url.strip()

    if getattr(settings, "local_llm_use_native_ollama", True) and _should_try_ollama_native_chat(
        openai_base
    ):
        root = _ollama_root_from_openai_base_url(openai_base)
        if root:
            try:
                return _invoke_ollama_native_chat(system, user, root)
            except Exception:
                pass

    extra_body: dict[str, Any] | None = None
    if getattr(settings, "local_llm_json_response", True):
        u = openai_base.lower()
        if "11434" in u or "ollama" in u:
            extra_body = {"format": "json"}
    llm = ChatOpenAI(
        model=settings.local_llm_model.strip(),
        api_key=(settings.local_llm_api_key or "ollama").strip() or "ollama",
        base_url=base,
        temperature=temp,
        timeout=timeout,
        max_retries=0,
        **({"extra_body": extra_body} if extra_body else {}),
    )
    msg = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    out = _message_content_as_str(msg)
    print_chat_model_io(
        f"本地OpenAI兼容剧本 base={base} model={settings.local_llm_model.strip()}",
        system,
        user,
        out,
    )
    return out


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
                out = _message_content_as_str(msg)
                print_chat_model_io(
                    f"OpenAI剧本 model={getattr(settings, 'openai_script_model', 'gpt-4o')}",
                    system,
                    user,
                    out,
                )
                return out
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
    # 完整匹配 openai_fallback_local 须先于 local（避免日后误用子串判断踩坑）
    if mode == "openai_fallback_local":
        try:
            return _invoke_openai_llm_with_429_backoff(system, user)
        except Exception:
            if _local_llm_configured():
                return _invoke_local_llm(system, user)
            raise
    if mode == "local":
        return _invoke_local_llm(system, user)
    return _invoke_openai_llm_with_429_backoff(system, user)


def script_llm_mode_is_local() -> bool:
    """True 表示 SCRIPT_LLM_MODE=local：剧本只走 LOCAL_LLM_*，不自动改调 QWEN_*。"""
    return (getattr(settings, "script_llm_mode", "") or "").lower().strip() == "local"


def _qwen_base_is_local(base: str) -> bool:
    b = (base or "").lower()
    return "127.0.0.1" in b or "localhost" in b or "0.0.0.0" in b


def qwen_configured() -> bool:
    base = (getattr(settings, "qwen_base_url", "") or "").strip()
    if not base:
        return False
    key = (getattr(settings, "qwen_api_key", "") or "").strip()
    if key:
        return True
    return _qwen_base_is_local(base)


def _invoke_qwen_llm(system: str, user: str) -> str:
    if not qwen_configured():
        raise RuntimeError(
            "未启用剧本兜底：请配置 QWEN_BASE_URL；非本机地址须同时设置 QWEN_API_KEY"
        )
    base = (settings.qwen_base_url or "").strip().rstrip("/")
    key = settings.qwen_api_key.strip() or "ollama"
    model = (settings.qwen_script_model or "qwen:0.5b").strip()
    timeout = float(getattr(settings, "qwen_timeout_sec", 120.0) or 120.0)
    if not base:
        raise RuntimeError("QWEN_BASE_URL 为空")
    llm = ChatOpenAI(
        model=model,
        api_key=key,
        base_url=base,
        temperature=0.85,
        timeout=timeout,
        max_retries=0,
    )
    msg = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    out = _message_content_as_str(msg)
    print_chat_model_io(
        f"Qwen剧本兜底 base={base} model={model}",
        system,
        user,
        out,
    )
    return out


def _invoke_script_llm(system: str, user: str, *, qwen_only: bool = False) -> str:
    """openai / openai_fallback_local：失败或空响应时可切 QWEN_*。local 模式不切 Qwen（避免同一 Ollama 被当「兜底」再打一遍）。"""
    if qwen_only:
        return _invoke_qwen_llm(system, user)
    local_only = script_llm_mode_is_local()
    try:
        text = _invoke_llm(system, user)
    except Exception:
        if qwen_configured() and not local_only:
            return _invoke_qwen_llm(system, user)
        raise
    if not (text or "").strip():
        if qwen_configured() and not local_only:
            return _invoke_qwen_llm(system, user)
        if local_only:
            raise RuntimeError("本地剧本模型返回为空，请检查 Ollama 与 LOCAL_LLM_*")
    return text


def generate_script(
    theme: str,
    style: str,
    duration: int,
    rag_context: Optional[str] = None,
    synopsis: Optional[str] = None,
    series_id: Optional[str] = None,
    episode: int = 1,
    *,
    qwen_only: bool = False,
) -> list[dict[str, Any]]:
    """
    生成短剧分镜。两步：大纲 + 分镜（可配置关闭为单步）。
    synopsis：用户故事简介，会强约束剧情走向。
    series_id + episode：连续剧模式，从 Chroma 拉历史并写回本集摘要。
    qwen_only：为 True 时整段只调用兜底 Qwen（主模型失败后的重试）。
    """
    num_scenes = _num_scenes_for_duration(duration)
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
        content = _invoke_script_llm(
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
            qwen_only=qwen_only,
        )
        raw = _parse_scenes_from_response(content)
        scenes = [_normalize_scene(i, s) for i, s in enumerate(raw) if isinstance(s, dict)]
        scenes = _maybe_expand_underfilled_scenes(scenes, num_scenes, duration)
        scenes = _split_oversized_time_scenes(scenes, duration=duration)
        if not scenes:
            if not qwen_only and qwen_configured() and not script_llm_mode_is_local():
                return generate_script(
                    theme,
                    style,
                    duration,
                    rag_context,
                    synopsis,
                    series_id,
                    episode,
                    qwen_only=True,
                )
            raise RuntimeError(
                _ERR_LOCAL_SCENES_PARSE
                if script_llm_mode_is_local()
                else "未能解析出分镜，请重试或检查本地 Qwen/Ollama 与 QWEN_* 配置"
            )
        try:
            _save_script_memory(theme, style, series_id, ep, scenes, None)
        except Exception:
            pass
        return scenes

    outline_raw = _invoke_script_llm(
        DIRECTOR_SYSTEM,
        OUTLINE_PROMPT.format(
            theme=theme,
            style=style,
            duration=duration,
            synopsis_section=syn_sec,
            series_section=ser_sec,
            rag_block=rag_block,
        ),
        qwen_only=qwen_only,
    )
    outline_obj = _parse_json_object(outline_raw)
    if not _outline_json_usable(outline_obj):
        outline_obj = {
            "hook_first_3s": f"{theme}开场即冲突",
            "beats": ["羞辱", "反击", "爆发", "反杀"],
            "outline": f"{theme}，{style}向短视频爽点。",
        }
    outline_json = json.dumps(outline_obj, ensure_ascii=False)

    content = _invoke_script_llm(
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
        qwen_only=qwen_only,
    )
    raw = _parse_scenes_from_response(content)
    scenes = [_normalize_scene(i, s) for i, s in enumerate(raw) if isinstance(s, dict)]
    if not scenes and script_llm_mode_is_local() and not qwen_only:
        legacy_user = LEGACY_PROMPT.format(
            theme=theme,
            style=style,
            duration=duration,
            num_scenes=num_scenes,
            synopsis_section=syn_sec,
            series_section=ser_sec,
            rag_block=rag_block,
        )
        content_fb = _invoke_script_llm(DIRECTOR_SYSTEM, legacy_user, qwen_only=qwen_only)
        raw = _parse_scenes_from_response(content_fb)
        scenes = [_normalize_scene(i, s) for i, s in enumerate(raw) if isinstance(s, dict)]
    scenes = _maybe_expand_underfilled_scenes(scenes, num_scenes, duration)
    scenes = _split_oversized_time_scenes(scenes, duration=duration)
    if not scenes:
        if not qwen_only and qwen_configured() and not script_llm_mode_is_local():
            return generate_script(
                theme,
                style,
                duration,
                rag_context,
                synopsis,
                series_id,
                episode,
                qwen_only=True,
            )
        raise RuntimeError(
            _ERR_LOCAL_SCENES_PARSE
            if script_llm_mode_is_local()
            else "未能解析出分镜，请重试或检查本地 Qwen/Ollama 与 QWEN_* 配置"
        )

    try:
        _save_script_memory(theme, style, series_id, ep, scenes, outline_obj)
    except Exception:
        pass

    return scenes
