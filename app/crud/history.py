"""videos 表：创建任务、更新状态、列表。"""
from typing import Optional

from sqlmodel import Session, select

from app.db import engine
from app.db_models import Video


def create_pending(job_id: str, theme: str, style: str, duration: int) -> None:
    with Session(engine) as session:
        session.add(
            Video(
                job_id=job_id,
                theme=theme,
                style=style,
                duration=duration,
                status="pending",
            )
        )
        session.commit()


def mark_running(job_id: str) -> None:
    with Session(engine) as session:
        row = session.exec(select(Video).where(Video.job_id == job_id)).first()
        if row:
            row.status = "running"
            session.add(row)
            session.commit()


def mark_completed(job_id: str, video_url: str, cover_url: str) -> None:
    with Session(engine) as session:
        row = session.exec(select(Video).where(Video.job_id == job_id)).first()
        if row:
            row.status = "completed"
            row.video_url = video_url
            row.cover_url = cover_url
            row.error = None
            session.add(row)
            session.commit()


def mark_failed(job_id: str, error: str) -> None:
    with Session(engine) as session:
        row = session.exec(select(Video).where(Video.job_id == job_id)).first()
        if row:
            row.status = "failed"
            row.error = error[:4000]
            session.add(row)
            session.commit()


def record_sync_completed(
    job_id: str,
    theme: str,
    style: str,
    duration: int,
    video_url: str,
    cover_url: str,
) -> None:
    """同步接口生成完成后写入历史（若 job_id 已存在则更新）。"""
    with Session(engine) as session:
        row = session.exec(select(Video).where(Video.job_id == job_id)).first()
        if row:
            row.theme = theme
            row.style = style
            row.duration = duration
            row.status = "completed"
            row.video_url = video_url
            row.cover_url = cover_url
            row.error = None
            session.add(row)
        else:
            session.add(
                Video(
                    job_id=job_id,
                    theme=theme,
                    style=style,
                    duration=duration,
                    status="completed",
                    video_url=video_url,
                    cover_url=cover_url,
                )
            )
        session.commit()


def list_recent(limit: int = 50, status: Optional[str] = None) -> list[Video]:
    with Session(engine) as session:
        stmt = select(Video)
        if status:
            stmt = stmt.where(Video.status == status)
        stmt = stmt.order_by(Video.created_at.desc()).limit(limit)
        return list(session.exec(stmt).all())
