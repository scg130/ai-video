"""Chroma 剧情记忆：系列短剧参考与写入。"""
import time
from pathlib import Path

from app.config import settings

_COLLECTION = "drama_series"
_client = None
_coll = None


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


def save_episode(theme: str, style: str, hook: str, outline: str, scenes_summary: str) -> None:
    c = _collection()
    if c is None:
        return
    try:
        doc = f"题材：{theme}\n风格：{style}\n开头钩子：{hook}\n大纲：{outline}\n分镜摘要：{scenes_summary}"[:20000]
        uid = f"{theme}_{int(time.time() * 1000)}"
        c.add(ids=[uid], documents=[doc], metadatas=[{"theme": theme, "style": style}])
    except Exception:
        pass
