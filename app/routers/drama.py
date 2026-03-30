"""短剧 API：同步、异步队列、前端用 /api/generate + /api/status + /api/history。"""
import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.crud import history as hist
from app.schemas import (
    DraftScriptRequest,
    DraftScriptResponse,
    GenerateApiResponse,
    GenerateShortDramaRequest,
    GenerateShortDramaResponse,
    GenerateVideoRequest,
    HistoryVideoItem,
    JobEnqueueResponse,
    JobStatusResponse,
    OneLinerExpandRequest,
    OneLinerExpandResponse,
    PublicStatusResponse,
)
from app.services.openai_keys import OpenAIAllKeysFailedError, OpenAINoKeysError
from app.services.pipeline_service import run_pipeline
from app.services.script_service import (
    build_fallback_draft_scenes,
    expand_from_one_liner,
    generate_script,
    normalize_scenes_list,
)
from app.services.visual_prompt import build_visual_prompt


def _openai_unavailable_response(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "openai_unavailable",
            "message": str(exc),
            "script": [],
        },
    )

router = APIRouter(prefix="/api", tags=["short_drama"])


def _enqueue_pipeline(
    theme: str,
    style: str,
    duration: int,
    script: list | None = None,
    series_id: str | None = None,
    episode: int = 1,
) -> str:
    """script=None：服务端现写剧本；否则使用用户提交的剧本。series_id+episode 用于连续剧 Chroma。"""
    sid = (series_id or "").strip() or None
    ep = max(1, int(episode))
    if settings.use_celery:
        try:
            from app.tasks_drama import generate_drama_task
        except ImportError as e:
            raise HTTPException(status_code=503, detail=f"Celery 未就绪: {e}")
        job_id = uuid.uuid4().hex
        hist.create_pending(job_id, theme, style, duration)
        generate_drama_task.apply_async(
            args=(theme, style, duration, script, sid, ep),
            task_id=job_id,
        )
        return job_id

    from app.queue.worker import enqueue_async

    return enqueue_async(theme, style, duration, script, sid, ep)


def _map_public_status(internal: str) -> str:
    if internal in ("completed", "SUCCESS"):
        return "done"
    if internal in ("failed", "FAILURE"):
        return "failed"
    if internal in ("running", "STARTED"):
        return "running"
    return "pending"


@router.post("/script/draft", response_model=DraftScriptResponse)
async def api_script_draft(req: DraftScriptRequest):
    """
    第一步：根据主题、风格、故事简介由大模型生成分镜剧本。
    失败时仍返回 HTTP 200，script 为可编辑兜底分镜，并带 ok=false / fallback=true。
    """
    loop = asyncio.get_event_loop()
    try:
        scenes = await loop.run_in_executor(
            None,
            lambda: generate_script(
                theme=req.theme,
                style=req.style,
                duration=req.duration,
                synopsis=req.synopsis or None,
                series_id=(req.series_id or "").strip() or None,
                episode=max(1, int(req.episode)),
            ),
        )
        return DraftScriptResponse(script=scenes)
    except (OpenAINoKeysError, OpenAIAllKeysFailedError) as e:
        fb = build_fallback_draft_scenes(
            req.theme, req.style, req.duration, req.synopsis or None
        )
        return DraftScriptResponse(
            script=fb,
            ok=False,
            fallback=True,
            error_code="openai_unavailable",
            message=str(e),
        )
    except Exception as e:
        fb = build_fallback_draft_scenes(
            req.theme, req.style, req.duration, req.synopsis or None
        )
        return DraftScriptResponse(
            script=fb,
            ok=False,
            fallback=True,
            error_code="draft_failed",
            message=str(e),
        )


def _fallback_one_liner_scenes(line: str, style: str, duration: int) -> list[dict]:
    theme = line[:24] if len(line) > 24 else line or "短剧"
    fb = build_fallback_draft_scenes(theme, style, duration, line or None)
    out: list[dict] = []
    for n in fb:
        d = str(n.get("dialogue") or "").strip() or "……"
        out.append(
            {
                **n,
                "image_prompt": build_visual_prompt(n)[:4000],
                "voice_text": d[:4096],
            }
        )
    return out


@router.post("/script/from-one-liner", response_model=OneLinerExpandResponse)
async def api_script_from_one_liner(req: OneLinerExpandRequest):
    """
    一句话扩写：返回剧本梗概、分镜列表；每镜含 image_prompt（文生图）、voice_text（配音）。
    失败时 HTTP 200 + ok=false、fallback=true，body 仍为可编辑占位。
    """
    line = (req.line or "").strip()
    if not line:
        raise HTTPException(status_code=400, detail="line 不能为空")
    loop = asyncio.get_event_loop()
    try:
        out = await loop.run_in_executor(
            None,
            lambda: expand_from_one_liner(line=line, style=req.style, duration=req.duration),
        )
        return OneLinerExpandResponse(script=out["script"], scenes=out["scenes"])
    except (OpenAINoKeysError, OpenAIAllKeysFailedError) as e:
        scenes = _fallback_one_liner_scenes(line, req.style, req.duration)
        return OneLinerExpandResponse(
            script=line,
            scenes=scenes,
            ok=False,
            fallback=True,
            error_code="openai_unavailable",
            message=str(e),
        )
    except Exception as e:
        scenes = _fallback_one_liner_scenes(line, req.style, req.duration)
        return OneLinerExpandResponse(
            script=line,
            scenes=scenes,
            ok=False,
            fallback=True,
            error_code="expand_failed",
            message=str(e),
        )


@router.post("/generate_video", response_model=GenerateApiResponse)
async def api_generate_video(req: GenerateVideoRequest):
    """
    第二步：提交用户编辑后的分镜 JSON，异步生成视频。
    返回 job_id 后轮询 GET /api/status/{job_id}。
    """
    try:
        normalize_scenes_list(req.script)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    job_id = _enqueue_pipeline(req.theme, req.style, req.duration, req.script)
    return GenerateApiResponse(job_id=job_id)


@router.post("/generate", response_model=GenerateApiResponse)
async def api_generate(req: GenerateShortDramaRequest):
    """
    一键异步（不写简介、不经过编辑）：服务端自动生成剧本再出片。
    若要走「简介→编辑→出片」，请用 /api/script/draft + /api/generate_video。
    """
    job_id = _enqueue_pipeline(
        req.theme,
        req.style,
        req.duration,
        None,
        (req.series_id or "").strip() or None,
        max(1, int(req.episode)),
    )
    return GenerateApiResponse(job_id=job_id)


@router.get("/status/{job_id}", response_model=PublicStatusResponse)
async def api_status(job_id: str):
    """轮询任务状态：status 为 done / failed / running / pending。"""
    if settings.use_celery:
        try:
            from celery.result import AsyncResult
            from app.celery_app import celery_app
        except ImportError:
            raise HTTPException(status_code=503, detail="Celery 未安装")
        r = AsyncResult(job_id, app=celery_app)
        st = r.state
        if st == "PENDING":
            return PublicStatusResponse(job_id=job_id, status="pending")
        if st == "STARTED":
            return PublicStatusResponse(job_id=job_id, status="running")
        if st == "SUCCESS":
            res = r.result or {}
            return PublicStatusResponse(
                job_id=job_id,
                status="done",
                video_url=res.get("video_url"),
                cover=res.get("cover"),
            )
        if st == "FAILURE":
            err = str(r.info) if r.info else "unknown"
            return PublicStatusResponse(job_id=job_id, status="failed", error=err)
        return PublicStatusResponse(job_id=job_id, status=_map_public_status(st))

    from app.queue.job_store import get_job

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    res = job.result or {}
    return PublicStatusResponse(
        job_id=job.job_id,
        status=_map_public_status(job.status),
        video_url=res.get("video_url"),
        cover=res.get("cover"),
        error=job.error,
    )


@router.get("/history", response_model=list[HistoryVideoItem])
async def api_history(limit: int = 40):
    """视频墙：按时间倒序返回历史记录。"""
    rows = hist.list_recent(limit=min(limit, 100))
    return [
        HistoryVideoItem(
            job_id=r.job_id,
            theme=r.theme,
            style=r.style,
            duration=r.duration,
            video_url=r.video_url,
            cover_url=r.cover_url,
            status=r.status,
            created_at=r.created_at.isoformat() + "Z" if r.created_at else "",
        )
        for r in rows
    ]


@router.delete("/history/{job_id}")
async def api_history_delete(job_id: str):
    """从视频墙删除一条记录，并删除 output/temp 下对应成片目录。"""
    if not hist.is_safe_job_id(job_id):
        raise HTTPException(status_code=400, detail="无效的 job_id")
    ok = hist.delete_by_job_id(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    hist.remove_job_artifacts(job_id)
    if not settings.use_celery:
        from app.queue.job_store import forget_job

        forget_job(job_id)
    return {"ok": True, "job_id": job_id}


@router.post("/generate_short_drama", response_model=GenerateShortDramaResponse)
async def generate_short_drama(req: GenerateShortDramaRequest):
    """同步生成（调试或小流量）。"""
    try:
        video_path, cover_path, script = await run_pipeline(
            theme=req.theme,
            style=req.style,
            duration=req.duration,
            bgm_path=None,
            series_id=(req.series_id or "").strip() or None,
            episode=max(1, int(req.episode)),
        )
    except (OpenAINoKeysError, OpenAIAllKeysFailedError) as e:
        raise _openai_unavailable_response(e) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成失败: {str(e)}") from e

    video_url = f"/static/{video_path.parent.name}/{video_path.name}"
    cover_url = f"/static/{cover_path.parent.name}/{cover_path.name}"
    hist.record_sync_completed(
        video_path.parent.name,
        req.theme,
        req.style,
        req.duration,
        video_url,
        cover_url,
    )
    return GenerateShortDramaResponse(
        video_url=video_url,
        cover=cover_url,
        script=script,
    )


@router.post("/jobs", response_model=JobEnqueueResponse)
async def enqueue_job(req: GenerateShortDramaRequest):
    """兼容旧路径，等价于 POST /api/generate（自动生成剧本）。"""
    job_id = _enqueue_pipeline(
        req.theme,
        req.style,
        req.duration,
        None,
        (req.series_id or "").strip() or None,
        max(1, int(req.episode)),
    )
    return JobEnqueueResponse(job_id=job_id)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def job_status(job_id: str):
    """兼容旧路径；状态为内部枚举（非 done）。"""
    if settings.use_celery:
        try:
            from celery.result import AsyncResult
            from app.celery_app import celery_app
        except ImportError:
            raise HTTPException(status_code=503, detail="Celery 未安装")
        r = AsyncResult(job_id, app=celery_app)
        st = r.state
        if st == "PENDING":
            return JobStatusResponse(job_id=job_id, status="pending")
        if st == "STARTED":
            return JobStatusResponse(job_id=job_id, status="running")
        if st == "SUCCESS":
            res = r.result or {}
            return JobStatusResponse(
                job_id=job_id,
                status="completed",
                video_url=res.get("video_url"),
                cover=res.get("cover"),
                script=res.get("script"),
            )
        if st == "FAILURE":
            err = str(r.info) if r.info else "unknown"
            return JobStatusResponse(job_id=job_id, status="failed", error=err)
        return JobStatusResponse(job_id=job_id, status=st.lower())

    from app.queue.job_store import get_job

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    res = job.result or {}
    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        video_url=res.get("video_url"),
        cover=res.get("cover"),
        script=res.get("script"),
        error=job.error,
    )


def register_static(app, output_dir: Path):
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory=str(output_dir)), name="static")
