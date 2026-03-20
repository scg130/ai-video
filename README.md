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
  文生图 / 文生视频（每镜一图，或 ComfyUI：CogVideoX / AnimateDiff 每镜一段）
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
| `VISUAL_MODE` | `images`（默认）、`cogvideox`、`animatediff`（后两者为 ComfyUI 文生视频） |
| `COMFYUI_BASE_URL` | ComfyUI API，默认 `http://127.0.0.1:8188` |
| `COGVIDEOX_WORKFLOW_PATH` | CogVideoX API workflow JSON 绝对路径 |
| `ANIMATEDIFF_WORKFLOW_PATH` | AnimateDiff API workflow JSON 绝对路径 |

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

**说明**：若 WebUI 开了登录或 `--api-auth`，需在 `SD_WEBUI_BASE_URL` 里带 Basic 认证或我们后续再加请求头配置。

---

## 文生视频：ComfyUI + CogVideoXWrapper（本地）

使用 **[ComfyUI-CogVideoXWrapper](https://github.com/kijai/ComfyUI-CogVideoXWrapper)** 在本地 ComfyUI 中生成 **每镜一段短视频**，再与 TTS、字幕拼接。

1. 按 **`docs/cogvideox_local_deploy.md`** 安装 ComfyUI、克隆扩展、下载模型，在界面跑通后 **导出 API 格式** workflow JSON。
2. `.env` 设置：

   ```env
   VISUAL_MODE=cogvideox
   COMFYUI_BASE_URL=http://127.0.0.1:8188
   COGVIDEOX_WORKFLOW_PATH=/你的路径/cogvideox_api.json
   ```

3. 若提示词未写入正确节点，配置 `COGVIDEOX_PROMPT_NODE_ID` / `COGVIDEOX_NEGATIVE_NODE_ID`（与 API JSON 中的 node id 一致）。

详细步骤见 **`docs/cogvideox_local_deploy.md`**。

---

## 文生视频：ComfyUI + AnimateDiff（本地）

典型节点链：**Prompt → Load Checkpoint → AnimateDiff Loader → Sampler → Video Combine**（具体节点名随扩展版本而定）。

1. 推荐安装 **[ComfyUI-AnimateDiff-Evolved](https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved)**（或你熟悉的 AnimateDiff 节点包），在界面跑通后 **导出 API** workflow。
2. `.env`：

   ```env
   VISUAL_MODE=animatediff
   COMFYUI_BASE_URL=http://127.0.0.1:8188
   ANIMATEDIFF_WORKFLOW_PATH=/你的路径/animatediff_api.json
   ```

3. 提示词节点无法自动识别时，设置 `ANIMATEDIFF_PROMPT_NODE_ID` / `ANIMATEDIFF_NEGATIVE_NODE_ID`。

详细步骤见 **`docs/animatediff_local_deploy.md`**。与 CogVideoX 共用 **`app/services/comfyui_common.py`**（ComfyUI API 调度）。

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
│       ├── comfyui_common.py         # ComfyUI API 公共逻辑
│       ├── comfyui_cogvideox_service.py
│       ├── comfyui_animatediff_service.py  # AnimateDiff 文生视频
│       ├── video_service.py    # FFmpeg 剪辑（含视频片段拼接）
│       └── pipeline_service.py # 一键流水线
├── docs/
│   ├── cogvideox_local_deploy.md
│   └── animatediff_local_deploy.md
├── output/
├── temp/
├── .env.example
├── requirements.txt
└── README.md
```
