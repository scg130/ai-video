# 斩仙台短剧一键生成

输入关键词（斩仙台/修仙/复仇）→ **GPT 剧本** → **TTS 配音** → **文生图** → **FFmpeg 自动剪辑（字幕+转场+BGM）** → 导出短视频（抖音风）。

## 架构

```
输入 theme / style / duration
        ↓
  GPT 生成分镜脚本（JSON）
        ↓
  TTS 生成配音（OpenAI / 内部 API）
        ↓
  文生图（每镜一图：OpenAI DALL·E 或本地 SD WebUI）
        ↓
  FFmpeg：图序列 + 配音 + SRT 字幕 + 可选 BGM
        ↓
  成片 MP4 + 封面
```

## 环境

- Python 3.11+
- FFmpeg（需已安装并加入 PATH）
- OpenAI API Key（剧本 + TTS；图像可选本地 SD）

## 安装

```bash
cd ai-video
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY
```

## 配置 (.env)

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | 必填，用于 GPT 剧本、TTS、文生图 |
| `USE_OPENAI_TTS` | 默认 true；false 时用内部 TTS，需配 `TTS_BASE_URL` |
| `IMAGE_PROVIDER` | `openai`（DALL·E）或 `sd_webui`（本地 SD） |
| `USE_OPENAI_IMAGE` | 旧配置：`false` 时等同走本地 SD（与 `IMAGE_PROVIDER=sd_webui` 二选一即可） |
| `SD_WEBUI_BASE_URL` | 本地 WebUI 地址，默认 `http://127.0.0.1:7860` |
| `SD_STEPS` / `SD_WIDTH` / `SD_HEIGHT` | 采样步数、分辨率（竖屏建议 512×768） |
| `OUTPUT_DIR` | 成片输出目录，默认 `./output` |
| `TEMP_DIR` | 临时文件，默认 `./temp` |

## 运行

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API 文档：http://localhost:8000/docs  
- 一键生成：`POST /api/generate_short_drama`

## API 示例

**请求**

```http
POST /api/generate_short_drama
Content-Type: application/json

{
  "theme": "斩仙台复仇",
  "style": "爽文",
  "duration": 60
}
```

**响应**

```json
{
  "video_url": "/static/<job_id>/short_drama.mp4",
  "cover": "/static/<job_id>/cover.png",
  "script": [
    { "time": "0-5s", "scene": "...", "dialogue": "...", "emotion": "..." }
  ]
}
```

成片与封面通过 `video_url`、`cover` 路径在站内访问（如 `http://localhost:8000/static/<job_id>/short_drama.mp4`）。

## 本地 Stable Diffusion（AUTOMATIC1111 WebUI）

1. **安装并启动 WebUI**，必须带 **API** 参数，例如：

   ```bash
   # macOS / Linux 示例
   ./webui.sh --api
   # Windows
   webui-user.bat  # 在启动参数里加 --api
   ```

2. 浏览器能打开 `http://127.0.0.1:7860`，并在 WebUI 里选好 **Checkpoint 模型**（与画风一致）。

3. **`.env` 配置**：

   ```env
   IMAGE_PROVIDER=sd_webui
   SD_WEBUI_BASE_URL=http://127.0.0.1:7860
   SD_WIDTH=512
   SD_HEIGHT=768
   ```

4. 再调 `POST /api/generate_short_drama`，出图会走 `POST /sdapi/v1/txt2img`，无需 OpenAI 图像 Key。

**说明**：ComfyUI、InvokeAI 等需另接 API；当前实现对接的是 **A1111 标准 txt2img**。若 WebUI 开了登录或 `--api-auth`，需在 `SD_WEBUI_BASE_URL` 里带 Basic 认证或我们后续再加请求头配置。

## 扩展

- **TTS**：在 `app/services/tts_service.py` 中接 CosyVoice / GPT-SoVITS，按 `voice_id`、情绪区分角色。
- **文生图/文生视频**：本地 SD 已接 WebUI；还可扩展 Pika、Runway、SVD 等。
- **Chroma**：在 `app/config.py` 已预留 `chroma_persist_dir`，可做剧情风格记忆与 RAG。
- **BGM**：在 `run_pipeline` 中传入 `bgm_path`（或从上传/配置读取），即可混入背景音乐。

## 目录结构

```
ai-video/
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 配置
│   ├── schemas.py           # 请求/响应模型
│   ├── routers/
│   │   └── drama.py         # POST /api/generate_short_drama
│   └── services/
│       ├── script_service.py   # GPT 分镜脚本
│       ├── subtitle_service.py # 脚本 → SRT
│       ├── tts_service.py      # TTS
│       ├── image_service.py    # 文生图
│       ├── video_service.py    # FFmpeg 剪辑
│       └── pipeline_service.py # 一键流水线
├── output/                  # 成片（按 job_id 分目录）
├── temp/                    # 临时文件
├── .env.example
├── requirements.txt
└── README.md
```
