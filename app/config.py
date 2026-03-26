"""应用配置"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # GPT（OPENAI_API_KEY 单 Key；OPENAI_API_KEYS 多 Key，逗号/空格/换行分隔，与单 Key 二选一或合并列表）
    openai_api_key: str = ""
    openai_api_keys: str = ""
    openai_script_model: str = "gpt-4o-mini"

    # 剧本：两步（大纲→分镜）+ 导演向 prompt
    script_two_step: bool = True
    # 剧本 LLM：openai | local | openai_fallback_local（OpenAI 失败再试本地）
    script_llm_mode: str = "openai"
    # 本地 OpenAI 兼容接口（如 Ollama：http://127.0.0.1:11434/v1 ）
    local_llm_base_url: str = ""
    local_llm_model: str = "llama3.2"
    local_llm_api_key: str = "ollama"
    local_llm_timeout_sec: float = 120.0
    # OpenAI 剧本：遇 429 时同一 Key 指数退避重试次数（之后再换 Key）
    script_openai_429_max_retries: int = 4
    script_openai_429_base_delay_sec: float = 2.0

    # 流水线容错：单镜图/音/视频失败时用占位，尽量仍导出成片
    pipeline_fault_tolerant: bool = True

    # RAG / Chroma 系列记忆
    rag_enabled: bool = True

    # 画面 Prompt 后缀（文生图 / Comfy 共用）
    visual_prompt_suffix: str = ""

    # 成片分辨率（Ken Burns / xfade）
    video_output_width: int = 720
    video_output_height: int = 1280
    ffmpeg_ken_burns: bool = True
    ffmpeg_xfade: bool = True
    ffmpeg_xfade_duration: float = 0.5
    ffmpeg_fps: int = 24
    # 抖音风字幕（ASS BGR + 边框）
    ffmpeg_subtitle_style: str = (
        "Fontsize=28,Bold=1,PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,"
        "Outline=3,Shadow=1,MarginV=40"
    )

    # 封面大字（爆款标题）
    cover_promo_title: bool = True

    # 任务队列：Celery + Redis（false 时用内存队列见 /api/jobs）
    use_celery: bool = False
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"

    # 内存队列并发上限（USE_CELERY=false 时）；默认 1 = 用户请求入队后逐个处理
    queue_max_concurrent: int = 1

    # TTS
    tts_base_url: str = ""
    tts_api_key: str = ""
    use_openai_tts: bool = True
    tts_default_voice: str = "alloy"

    # 图像：openai（DALL·E）| sd_webui（本地 AUTOMATIC1111 --api）
    image_provider: str = "openai"  # openai | sd_webui
    stability_api_key: str = ""
    replicate_api_token: str = ""
    use_openai_image: bool = True  # 兼容旧配置：true 等价于 image_provider=openai

    # 本地 Stable Diffusion（WebUI）
    sd_webui_base_url: str = "http://127.0.0.1:7860"
    sd_negative_prompt: str = (
        "lowres, bad anatomy, bad hands, text, error, missing fingers, "
        "extra digit, fewer digits, cropped, worst quality, low quality, jpeg artifacts, blurry"
    )
    sd_steps: int = 28
    sd_width: int = 512
    sd_height: int = 768  # 竖屏
    sd_cfg_scale: float = 7.0
    sd_sampler_name: str = "DPM++ 2M Karras"

    # 画面来源：images | cogvideox | animatediff（后两者均为 ComfyUI 文生视频片段）
    visual_mode: str = "images"  # images | cogvideox | animatediff

    # ComfyUI（visual_mode=cogvideox / animatediff）
    comfyui_base_url: str = "http://127.0.0.1:8188"
    cogvideox_workflow_path: str = ""
    cogvideox_prompt_node_id: str = ""
    cogvideox_negative_node_id: str = ""
    cogvideox_negative_default: str = (
        "lowres, bad anatomy, blurry, watermark, text, worst quality, jpeg artifacts"
    )
    cogvideox_randomize_seed: bool = True

    # AnimateDiff：Prompt → Checkpoint → AnimateDiff Loader → Sampler → Video Combine
    animatediff_workflow_path: str = ""
    animatediff_prompt_node_id: str = ""
    animatediff_negative_node_id: str = ""
    animatediff_randomize_seed: bool = True

    # 路径
    output_dir: str = "./output"
    temp_dir: str = "./temp"

    # Chroma
    chroma_persist_dir: str = "./chroma_db"

    # SQLite 历史（视频墙）
    database_url: str = "sqlite:///./data/videos.db"

    @property
    def output_path(self) -> Path:
        p = Path(self.output_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def temp_path(self) -> Path:
        p = Path(self.temp_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
