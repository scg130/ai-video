import time
from dataclasses import dataclass, field
from typing import Any, Optional

_jobs: dict[str, "JobState"] = {}


@dataclass
class JobState:
    job_id: str
    status: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    theme: str = ""
    style: str = ""
    duration: int = 60
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


def create_job(job_id: str, theme: str, style: str, duration: int) -> JobState:
    s = JobState(job_id=job_id, status="pending", theme=theme, style=style, duration=duration)
    _jobs[job_id] = s
    return s


def get_job(job_id: str) -> Optional[JobState]:
    return _jobs.get(job_id)


def set_running(job_id: str) -> None:
    if job_id in _jobs:
        _jobs[job_id].status = "running"
        _jobs[job_id].updated_at = time.time()


def set_completed(job_id: str, result: dict[str, Any]) -> None:
    if job_id in _jobs:
        _jobs[job_id].status = "completed"
        _jobs[job_id].result = result
        _jobs[job_id].updated_at = time.time()


def set_failed(job_id: str, error: str) -> None:
    if job_id in _jobs:
        _jobs[job_id].status = "failed"
        _jobs[job_id].error = error
        _jobs[job_id].updated_at = time.time()


def forget_job(job_id: str) -> None:
    """从内存队列状态中移除（用户删除历史后避免残留轮询）。"""
    _jobs.pop(job_id, None)
