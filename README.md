# 斩仙台短剧一键生成

输入**主题 / 风格 / 故事简介** → **GPT 生成分镜剧本**（可编辑）→ **提交后**走 **TTS** → **文生图/视频** → **FFmpeg（字幕+转场+BGM）** → 导出短视频（抖音风）。

## 架构

**成片流水线**由 [LangGraph](https://github.com/langchain-ai/langgraph) 编排（`app/graph/pipeline_graph.py`）：`init_paths` → `load_script` → `subtitles` → 按 `VISUAL_MODE` 条件分支（`media_cog` / `media_ad` / `media_img`）→ `assemble_*` → `finalize_cover`。对外仍通过 `app.services.pipeline_service.run_pipeline` 调用，行为与改造前一致，后续可在此图上扩展检查点、重试策略或人工审核节点。

```
输入 theme / style / duration / synopsis（故事简介）
        ↓
  GPT 生成分镜脚本（JSON，前端可编辑后再提交）
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
| `OPENAI_API_KEY` | 至少配置其一：单 Key，用于 GPT 剧本、OpenAI TTS、DALL·E |
| `OPENAI_API_KEYS` | 可选：多 Key（逗号/空格/换行分隔），与单 Key 合并去重；请求**轮询起始 Key**，401/403/429 时**自动换 Key** 重试 |
| `SCRIPT_LLM_MODE` | `openai`（默认）\|`local`\|`openai_fallback_local`\|`mamba`\|`openai_fallback_mamba`；`mamba` 走 `MAMBA_*`（OpenAI 兼容，适合 vLLM 托管 Mamba 等长上下文模型） |
| `SCRIPT_LLM_MODE_STRICT` | 默认 false：项目根 `.env` 中的 `SCRIPT_LLM_MODE`、`LOCAL_LLM_*`、`QWEN_*`、`MAMBA_*` **覆盖** shell 里已 `export` 的同名字段。设 `true` 时以环境变量为准（适合容器注入） |
| `LOCAL_LLM_BASE_URL` / `LOCAL_LLM_MODEL` | 本地 OpenAI 兼容端点，如 Ollama：`http://127.0.0.1:11434/v1` + `llama3.2` |
| `MAMBA_BASE_URL` / `MAMBA_MODEL` | 长上下文剧本端点（须带 `/v1` 或与 OpenAI 兼容的根 URL）；`MAMBA_API_KEY` 可选；`MAMBA_MAX_OUTPUT_TOKENS` 默认 8192 |
| `RAG_MATERIALS_*` | `RAG_MATERIALS_ENABLED`：是否检索「资料」库；`RAG_MATERIALS_TOP_K` / `RAG_MATERIALS_MAX_CHARS`：资料片段条数与长度上限 |
| `SCRIPT_OPENAI_429_MAX_RETRIES` / `SCRIPT_OPENAI_429_BASE_DELAY_SEC` | 剧本调用 OpenAI 遇 429 时，同一 Key 指数退避重试后再换 Key |
| `PIPELINE_FAULT_TOLERANT` | 默认 true：TTS/图/视频单镜失败时用静音或占位，尽量仍合成成片 |
| `QUEUE_MAX_CONCURRENT` | 内存队列并发（`USE_CELERY=false`），默认 **1**（任务严格排队逐个跑）；Celery 模式需在 worker 侧用 `-c 1` 若也要串行 |
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
| `DATABASE_URL` | 默认 `sqlite:///./data/videos.db`（历史记录 / 视频墙） |

## 运行

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- **Web 控制台**：http://localhost:8000/（`web/index.html`：**故事简介** → **生成剧本**（可编辑 JSON）→ **生成视频** → 轮询 / 视频墙）  
- API 文档：http://localhost:8000/docs  
- **推荐交互流程**：`POST /api/script/draft`（主题、风格、时长、简介）→ 用户编辑 `script` → `POST /api/generate_video`（同上 + `script` 数组）→ `GET /api/status/{job_id}`（`status === "done"`）→ `GET /api/history`  
- **参考资料入库**：`POST /api/rag/material`（`text` + 可选 `doc_id` / `tags`），与连续剧记忆一起在生成剧本时检索  
- **一键异步（不写简介、不编辑剧本）**：`POST /api/generate` → `GET /api/status/{job_id}` → `GET /api/history`  
- **同步**：`POST /api/generate_short_drama`（长耗时，易超时）  
- **兼容**：`POST /api/jobs` → `GET /api/jobs/{job_id}`  
  - `USE_CELERY=false`：进程内 asyncio + 信号量限流（`QUEUE_MAX_CONCURRENT`）  
  - `USE_CELERY=true`：需 Redis，另开 worker：`celery -A app.celery_app:celery_app worker -l info`  
- **数据库**：SQLite `data/videos.db`（`DATABASE_URL`），表 `videos`：`job_id, theme, style, duration, video_url, cover_url, status, created_at, error`

## 核心优化（剧本 / 队列 / TTS / 画面 / FFmpeg）

| 能力 | 说明 |
|------|------|
| **两步剧本** | `SCRIPT_TWO_STEP=true`：先大纲（钩子+节奏），再分镜 |
| **防 429 + 本地剧本** | `SCRIPT_LLM_MODE=openai_fallback_local` + `LOCAL_LLM_BASE_URL`（如 Ollama `/v1`）：OpenAI 429 时同 Key 退避（`SCRIPT_OPENAI_429_*`），失败再切本地；`local` 可仅用本地 |
| **流水线容错** | `PIPELINE_FAULT_TOLERANT=true`：单段 TTS 失败→静音；单张图失败→占位图；CogVideoX/AnimateDiff 单镜失败→黑场占位；剧本生成仍失败→模板分镜 |
| **RAG + 资料** | `RAG_ENABLED=true`：Chroma **连续剧记忆**（`drama_series`）+ **资料库**（`drama_materials`，可用 `POST /api/rag/material` 写入）；`app/services/script_context_chain.py` 用 LangChain `Runnable` 编排检索块再注入剧本 prompt |
| **Mamba-2 / 长上下文** | `SCRIPT_LLM_MODE=mamba` + `MAMBA_*` 指向 OpenAI 兼容服务；**本地部署**见 [docs/mamba2_local_deploy.md](docs/mamba2_local_deploy.md)（vLLM + `MAMBA_BASE_URL=http://127.0.0.1:8000/v1`）；`openai_fallback_mamba` 为 OpenAI 失败后再试 Mamba |
| **统一画面 Prompt** | `app/services/visual_prompt.py`：仙侠电影感 + scene/emotion/camera + cinematic 4k |
| **多角色 TTS** | `role`→OpenAI 声线：主角 onyx、反派 echo、女主 shimmer 等（可接 GPT-SoVITS / CosyVoice 扩展内部 API） |
| **FFmpeg** | Ken Burns（`FFMPEG_KEN_BURNS`）、片段间 `xfade`（`FFMPEG_XFADE`）、抖音风字幕样式 |
| **变长分镜** | 按分镜 `time`（如 `0-3s`）解析每段时长，对齐首镜「3 秒必炸」 |
| **封面大字** | `COVER_PROMO_TITLE`：首句台词/画面叠金色标题 |

## API 示例

**1）仅生成剧本（可编辑后再出片）**

```http
POST /api/script/draft
Content-Type: application/json

{
  "theme": "斩仙台复仇",
  "style": "爽文",
  "duration": 60,
  "synopsis": "主角被诬上斩仙台，真相反转后反杀仇敌。",
  "series_id": "zx_001",
  "episode": 3
}
```

`series_id` + `episode`（可选）：连续剧模式——从 Chroma 拉同系列历史，生成第 N 集（承接、不重复、冲突升级），并把本集摘要写回记忆。

响应体含 `script`（分镜数组），与 `generate_video` 的 `script` 格式一致。  
**失败兜底**：仍返回 **HTTP 200**，`ok: false`、`fallback: true`，`script` 为可编辑占位分镜，`error_code`（如 `openai_unavailable` / `draft_failed`）与 `message` 说明原因。

**2）用编辑后的剧本异步生成视频**

```http
POST /api/generate_video
Content-Type: application/json

{
  "theme": "斩仙台复仇",
  "style": "爽文",
  "duration": 60,
  "script": [
    { "time": "0-5s", "scene": "...", "dialogue": "...", "emotion": "..." }
  ]
}
```

返回 `{ "job_id": "..." }`，再轮询 `GET /api/status/{job_id}`。

**3）同步一键（长耗时，易超时）**

```http
POST /api/generate_short_drama
Content-Type: application/json

{
  "theme": "斩仙台复仇",
  "style": "爽文",
  "duration": 60
}
```

**响应（同步）**

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
├── web/
│   └── index.html           # 首页 UI（输入区 + 视频墙）
├── data/                    # SQLite（gitignore）
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 配置
│   ├── schemas.py           # 请求/响应模型
│   ├── db.py                # SQLModel 引擎
│   ├── db_models.py         # videos 表
│   ├── crud/history.py      # 历史读写
│   ├── celery_app.py        # Celery 应用（可选）
│   ├── tasks_drama.py       # Celery 任务
│   ├── queue/               # 内存异步队列
│   ├── routers/
│   │   └── drama.py         # 同步 /api/jobs /api/jobs/{id}
│   └── services/
│       ├── script_service.py   # 两步剧本 + 导演 prompt
│       ├── rag_service.py      # Chroma：连续剧 + 资料库
│       ├── script_context_chain.py  # LangChain：RAG 块编排
│       ├── visual_prompt.py    # 统一画面 prompt
│       ├── subtitle_service.py
│       ├── tts_service.py      # 多角色 TTS
│       ├── image_service.py
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
