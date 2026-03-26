"""数据库引擎与会话。"""
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

Path("data").mkdir(parents=True, exist_ok=True)
_connect_args = {"check_same_thread": False} if "sqlite" in settings.database_url else {}
engine = create_engine(settings.database_url, connect_args=_connect_args)


def init_db() -> None:
    from app.db_models import Video  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
