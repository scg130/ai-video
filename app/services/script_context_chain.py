"""
剧本上下文：Chroma 检索（连续剧记忆 + 题材检索 + 资料库）+ LangChain Runnable 编排。

长上下文生成由 SCRIPT_LLM_MODE=mamba 等配置指向的 OpenAI 兼容端点完成（如 vLLM 托管 Mamba）。
"""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.runnables import RunnableLambda

from app.config import settings
from app.services import rag_service


def compose_script_rag_block(
    theme: str,
    style: str,
    *,
    series_id: Optional[str] = None,
    episode: int = 1,
    rag_context: Optional[str] = None,
) -> str:
    """
    组装注入到剧本 prompt 的 RAG 块（与 generate_script 原逻辑一致，并叠加资料检索）。
    rag_context 非空时仅使用该字符串，不再查库。
    """
    if rag_context:
        return "\n\n" + rag_context.strip()
    if not getattr(settings, "rag_enabled", True):
        return ""

    blocks: list[str] = []
    sid = (series_id or "").strip()
    ep = max(1, int(episode))
    if sid:
        ctx = rag_service.get_story_context(sid, ep)
        if ctx:
            blocks.append(ctx)
    else:
        qc = rag_service.query_context(theme, style)
        if qc:
            blocks.append(qc)

    if getattr(settings, "rag_materials_enabled", True):
        mq = rag_service.query_materials(theme, style)
        if mq:
            blocks.append(mq)

    if not blocks:
        return ""
    return "\n\n" + "\n\n".join(blocks)


def _compose_from_dict(d: dict[str, Any]) -> str:
    return compose_script_rag_block(
        str(d.get("theme") or ""),
        str(d.get("style") or ""),
        series_id=d.get("series_id"),
        episode=int(d.get("episode") or 1),
        rag_context=d.get("rag_context"),
    )


# LangChain 调度入口：便于测试或在外层 LCEL 中串联
SCRIPT_CONTEXT_CHAIN = RunnableLambda(_compose_from_dict)
