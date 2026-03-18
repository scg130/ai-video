"""剧本生成：GPT 生成短剧分镜脚本（JSON）"""
import json
import re
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from app.config import settings

SCRIPT_SYSTEM = """你是一位专业的短剧编剧，擅长修仙、复仇、打脸类爽文。请严格按用户要求输出分镜脚本。"""

SCRIPT_PROMPT = """生成一个短视频剧本，题材：{theme}，风格：{style}。

要求：
1. 时长 {duration} 秒
2. 每 5 秒一个分镜，共 {num_scenes} 个分镜
3. 每个分镜包含：
   - 画面描述（scene）：具体场景、人物动作、镜头感
   - 人物台词（dialogue）：该镜头的对白，可空
   - 情绪（emotion）：如愤怒、冷笑、震惊等
4. 风格：爽文、反转、打脸，节奏紧凑。

只输出一个 JSON 数组，不要其他说明。格式示例：
[
  {{"time": "0-5s", "scene": "斩仙台下，众人围观", "dialogue": "今日便是你的死期！", "emotion": "冷酷"}},
  {{"time": "5-10s", "scene": "主角抬头，眼神锐利", "dialogue": "是吗？", "emotion": "冷笑"}}
]
"""


def _parse_scenes_from_response(text: str) -> list[dict[str, Any]]:
    """从模型输出中解析 JSON 数组（允许被 markdown 包裹）"""
    text = text.strip()
    # 去掉可能的 ```json ... ``` 包裹
    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 尝试找第一个 [ 到最后一个 ]
        start = text.find("[")
        end = text.rfind("]") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    return []


def generate_script(theme: str, style: str, duration: int) -> list[dict[str, Any]]:
    """
    生成短剧分镜脚本。
    返回 list[dict]，每项含 time, scene, dialogue, emotion。
    """
    num_scenes = max(1, duration // 5)
    prompt = SCRIPT_PROMPT.format(
        theme=theme,
        style=style,
        duration=duration,
        num_scenes=num_scenes,
    )
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        api_key=settings.openai_api_key,
        temperature=0.8,
    )
    msg = llm.invoke([HumanMessage(content=prompt)])
    content = msg.content if hasattr(msg, "content") else str(msg)
    raw = _parse_scenes_from_response(content)
    # 归一化为 dict 列表，保证字段存在
    scenes = []
    for i, s in enumerate(raw):
        if isinstance(s, dict):
            scenes.append({
                "time": s.get("time", f"{i*5}-{(i+1)*5}s"),
                "scene": s.get("scene", ""),
                "dialogue": s.get("dialogue", ""),
                "emotion": s.get("emotion", ""),
            })
    return scenes
