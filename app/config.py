"""应用配置"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # GPT
    openai_api_key: str = ""

    # TTS
    tts_base_url: str = ""
    tts_api_key: str = ""
    use_openai_tts: bool = True

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

    # 路径
    output_dir: str = "./output"
    temp_dir: str = "./temp"

    # Chroma
    chroma_persist_dir: str = "./chroma_db"

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
