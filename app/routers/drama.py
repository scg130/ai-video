"""短剧一键生成 API"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.schemas import GenerateShortDramaRequest, GenerateShortDramaResponse
from app.services.pipeline_service import run_pipeline

router = APIRouter(prefix="/api", tags=["short_drama"])


@router.post("/generate_short_drama", response_model=GenerateShortDramaResponse)
async def generate_short_drama(req: GenerateShortDramaRequest):
    """
    一键生成短剧视频。
    入参：theme, style, duration
    返回：video_url（相对路径或静态 URL）, cover, script
    """
    try:
        video_path, cover_path, script = await run_pipeline(
            theme=req.theme,
            style=req.style,
            duration=req.duration,
            bgm_path=None,  # 可选：从配置或上传获取 BGM
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成失败: {str(e)}")

    # 返回可访问路径：这里用相对路径，实际部署时可改为静态 URL
    video_url = f"/static/{video_path.parent.name}/{video_path.name}"
    cover_url = f"/static/{cover_path.parent.name}/{cover_path.name}"
    return GenerateShortDramaResponse(
        video_url=video_url,
        cover=cover_url,
        script=script,
    )


def register_static(app, output_dir: Path):
    """注册静态目录，使 /static/<job_id>/xxx 可访问输出文件。"""
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory=str(output_dir)), name="static")
