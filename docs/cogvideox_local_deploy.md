# ComfyUI-CogVideoXWrapper 本地部署（对接 ai-video 文生视频）

本项目的 **`VISUAL_MODE=cogvideox`** 分支会调用 **ComfyUI HTTP API**，把每个分镜的 `scene` 文案交给你在 ComfyUI 里配置好的 **CogVideoX** 工作流，生成分段视频后再用 FFmpeg 拼配音与字幕。

官方扩展仓库：**[kijai/ComfyUI-CogVideoXWrapper](https://github.com/kijai/ComfyUI-CogVideoXWrapper)**（模型、依赖以该仓库 `readme.md` 与 `requirements.txt` 为准）。

---

## 1. 安装 ComfyUI

按 [ComfyUI 官方说明](https://github.com/comfyanonymous/ComfyUI) 克隆并安装依赖，建议使用 **Python 3.10+** 与 **CUDA** 环境。

```bash
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI
pip install -r requirements.txt
```

---

## 2. 安装 CogVideoXWrapper

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/kijai/ComfyUI-CogVideoXWrapper.git
cd ComfyUI-CogVideoXWrapper
pip install -r requirements.txt
```

重启 ComfyUI 后，在界面中应能看到 CogVideoX 相关节点。仓库内 **`example_workflows`** 目录有示例工作流（Update8 后旧工作流可能不兼容，请优先用新版示例）。

---

## 3. 模型与依赖

- 按 **CogVideoXWrapper readme** 与 [模型表格](https://docs.google.com/spreadsheets/d/16eA6mSL8XkTcu9fSWkPSHfRIqyAKJbR1O99xnuGdCKY/edit) 下载对应 **CogVideoX / T5** 等文件到 ComfyUI 约定目录（如 `ComfyUI/models/CogVideo/`）。
- 注意 **diffusers** 等版本要求（以扩展内 `requirements.txt` 为准）。

---

## 4. 在 ComfyUI 中跑通并导出 API

1. 在 ComfyUI 中打开示例工作流，选好自己的模型，**能成功导出一段视频**。
2. 菜单 **Workflow → Export (API)**（或「导出 API 格式」），保存为 JSON，例如 `~/workflows/cogvideox_t2v_api.json`。
3. 记下 **正向提示词**所在节点的 **node id**（API JSON 里最外层 key，如 `"12"`）。若自动注入失败，在 `.env` 中填写：
   - `COGVIDEOX_PROMPT_NODE_ID=12`
   - 若有独立负向词节点：`COGVIDEOX_NEGATIVE_NODE_ID=...`

本服务会：

- 向该节点的 `inputs.text`（或 `inputs.prompt`）写入：`古风前缀 + 分镜 scene 描述`。
- 尝试在第一个类名含 **`Sampler`** 且带 **`seed`** 的节点上写入随机种子（可用 `COGVIDEOX_RANDOMIZE_SEED=false` 关闭）。

---

## 5. 启动 ComfyUI（开启 API）

```bash
cd ComfyUI
python main.py --listen 0.0.0.0 --port 8188
```

默认 API 地址：`http://127.0.0.1:8188`。

---

## 6. 配置 ai-video

`.env` 示例：

```env
VISUAL_MODE=cogvideox
COMFYUI_BASE_URL=http://127.0.0.1:8188
COGVIDEOX_WORKFLOW_PATH=/绝对路径/cogvideox_t2v_api.json
COGVIDEOX_PROMPT_NODE_ID=
COGVIDEOX_NEGATIVE_NODE_ID=
COGVIDEOX_RANDOMIZE_SEED=true
```

然后照常启动 FastAPI，调用 `POST /api/generate_short_drama`。

---

## 7. 行为说明

- **每个分镜**会排队调用一次 ComfyUI（耗时与显存占用远大于文生图），总时长 ≈ 单段耗时 × 分镜数。
- 输出优先识别 **`.mp4` / `.webm` / `.gif`**；若工作流只出图，会回退保存图片并由 FFmpeg 拉成固定时长片段再拼接。
- 每段会 **按 `seconds_per_clip`（默认 5s）裁剪** 后再拼接，以便与 TTS 分句对齐。

---

## 8. Docker / 远程 ComfyUI

- `COMFYUI_BASE_URL` 可填局域网或容器内服务名，如 `http://comfyui:8188`。
- GPU 需跑在 **ComfyUI 所在机器**；ai-video 容器本身可不占用 GPU。
