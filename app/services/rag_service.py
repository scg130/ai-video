"""Chroma 剧情记忆：系列短剧参考与写入（含连续剧 series_id + episode）。"""
import time
from pathlib import Path

from app.config import settings

_COLLECTION = "drama_series"
_client = None
_coll = None

# 检索上下文上限（字符）
RAG_CONTEXT_MAX_CHARS = 2000


def _collection():
    global _client, _coll
    if not getattr(settings, "rag_enabled", True):
        return None
    if _coll is not None:
        return _coll
    try:
        import chromadb
    except ImportError:
        return None
    persist = str(Path(settings.chroma_persist_dir).absolute())
    _client = chromadb.PersistentClient(path=persist)
    _coll = _client.get_or_create_collection(_COLLECTION, metadata={"hnsw:space": "cosine"})
    return _coll


def query_context(theme: str, style: str, top_k: int = 3) -> str:
    c = _collection()
    if c is None:
        return ""
    try:
        r = c.query(query_texts=[f"{theme} {style}"], n_results=min(top_k, 10))
        docs = (r.get("documents") or [[]])[0]
        if not docs:
            return ""
        return "【系列/历史剧情参考，可延续人设与伏笔，勿照搬】\n" + "\n---\n".join(docs)
    except Exception:
        return ""


def get_story_context(series_id: str, episode: int, top_k: int = 4) -> str:
    """
    按连续剧 ID 检索历史剧情；优先匹配「上一集」语义，用于第 N 集承接。
    """
    c = _collection()
    if c is None:
        return ""
    sid = (series_id or "").strip()
    if not sid:
        return ""
    prev_ep = max(1, episode - 1)
    query_text = (
        f"{sid} 第{prev_ep}集 剧情 冲突 伏笔"
        if episode > 1
        else f"{sid} 系列 世界观 人物关系"
    )
    docs: list[str] = []
    try:
        r = c.query(
            query_texts=[query_text],
            n_results=min(top_k, 10),
            where={"series_id": sid},
        )
        docs = (r.get("documents") or [[]])[0]
    except Exception:
        docs = []
    if not docs:
        try:
            r = c.query(query_texts=[query_text], n_results=min(top_k * 3, 30))
            all_docs = (r.get("documents") or [[]])[0]
            metas = (r.get("metadatas") or [[]])[0]
            for i, m in enumerate(metas or []):
                if isinstance(m, dict) and m.get("series_id") == sid and i < len(all_docs):
                    docs.append(all_docs[i])
                if len(docs) >= top_k:
                    break
        except Exception:
            return ""
    if not docs:
        return ""
    ctx = "\n---\n".join(docs)
    ctx = ctx[:RAG_CONTEXT_MAX_CHARS]
    return f"【历史剧情参考（承接第 {episode} 集，勿照搬原文）】\n{ctx}"


def save_episode(theme: str, style: str, hook: str, outline: str, scenes_summary: str) -> None:
    """无 series_id 的单集摘要（兼容旧逻辑）。"""
    c = _collection()
    if c is None:
        return
    try:
        doc = f"题材：{theme}\n风格：{style}\n开头钩子：{hook}\n大纲：{outline}\n分镜摘要：{scenes_summary}"[:20000]
        uid = f"{theme}_{int(time.time() * 1000)}"
        c.add(ids=[uid], documents=[doc], metadatas=[{"theme": theme, "style": style}])
    except Exception:
        pass


def save_series_episode(
    series_id: str,
    episode: int,
    theme: str,
    style: str,
    summary: str,
    world_state: str,
    hook: str = "",
    outline: str = "",
) -> None:
    """连续剧：写入 Chroma，供下一集检索。"""
    c = _collection()
    if c is None:
        return
    sid = (series_id or "").strip()
    if not sid:
        return
    try:
        doc = (
            f"系列ID：{sid}\n第{episode}集\n"
            f"题材：{theme}\n风格：{style}\n"
            f"开头钩子：{hook}\n大纲：{outline}\n"
            f"分镜摘要：{summary}\n世界状态：{world_state}"
        )[:20000]
        uid = f"{sid}_ep{episode}_{int(time.time() * 1000)}"
        c.add(
            ids=[uid],
            documents=[doc],
            metadatas=[
                {
                    "series_id": sid,
                    "episode": int(episode),
                    "theme": theme,
                    "style": style,
                }
            ],
        )
    except Exception:
        pass
