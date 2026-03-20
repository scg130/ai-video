# ComfyUI + AnimateDiff 本地部署（对接 ai-video）

当 **`VISUAL_MODE=animatediff`** 时，每个分镜会调用你在 ComfyUI 里配置好的 **AnimateDiff** 工作流，典型链路为：

```
Prompt（CLIPTextEncode）
    → Load Checkpoint
    → AnimateDiff Loader（或 Evolved 版对应节点）
    → KSampler
    → Video Combine（如 VHS_VideoCombine / 输出 gif、mp4）
```

---

## 1. 推荐扩展

常用实现之一：**[ComfyUI-AnimateDiff-Evolved](https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved)**  

在 `ComfyUI/custom_nodes` 下克隆，按仓库说明安装依赖；下载 **motion module**、**SD checkpoint** 到对应 `models` 目录。

（若使用其他 AnimateDiff 节点包，只要能在界面跑通并 **导出 API**，本服务同样适用。）

---

## 2. 在 ComfyUI 中搭工作流

1. 新建或打开示例工作流，确保包含：
   - **CheckpointLoaderSimple**（或等价加载器）
   - **AnimateDiff** 相关加载节点（名称随扩展版本变化，如 `ADE_AnimateDiffLoaderGen1` 等）
   - **CLIPTextEncode** 正向 / 负向
   - **KSampler**（或 KSampler Advanced）
   - **视频合成**节点（输出到 `output` 目录，常见为 **VHS** 系列 **Video Combine** 或扩展自带 Combine 节点）

2. 在界面中 **完整跑通一次**，确认 `ComfyUI/output` 下能生成 **gif / mp4** 等。

3. **Workflow → Export (API)**，保存为 JSON，例如 `~/workflows/animatediff_t2v_api.json`。

4. 若程序无法自动写入提示词，在 API JSON 中查看 **CLIPTextEncode** 的 **node id**（最外层 key），填入：
   - `ANIMATEDIFF_PROMPT_NODE_ID`
   - `ANIMATEDIFF_NEGATIVE_NODE_ID`

---

## 3. 启动 ComfyUI

```bash
cd ComfyUI
python main.py --listen 0.0.0.0 --port 8188
```

---

## 4. 配置 ai-video（`.env`）

```env
VISUAL_MODE=animatediff
COMFYUI_BASE_URL=http://127.0.0.1:8188
ANIMATEDIFF_WORKFLOW_PATH=/绝对路径/animatediff_t2v_api.json
ANIMATEDIFF_PROMPT_NODE_ID=
ANIMATEDIFF_NEGATIVE_NODE_ID=
ANIMATEDIFF_RANDOMIZE_SEED=true
```

负向提示词默认使用全局 **`SD_NEGATIVE_PROMPT`**（`app.config` 中 `sd_negative_prompt`）。

---

## 5. 与 CogVideoX 方案的区别

| 项目 | AnimateDiff | CogVideoXWrapper |
|------|-------------|------------------|
| 典型模型 | SD1.5 + motion module | CogVideoX 系列 |
| 显存/速度 | 相对轻（依分辨率与帧数） | 通常更重 |
| workflow | 需含 AnimateDiff Loader + Video Combine | 需 CogVideoX 节点链 |

两者均通过 **同一套 ComfyUI HTTP API**（`POST /prompt`）调度，仅 **workflow JSON** 与 **VISUAL_MODE** 不同。

---

## 6. 实现说明（代码）

- `app/services/comfyui_common.py`：加载 workflow、注入 prompt/seed、轮询结果、下载视频/图。
- `app/services/comfyui_animatediff_service.py`：AnimateDiff 专用入口与分镜循环。
- `app/services/pipeline_service.py`：`VISUAL_MODE=animatediff` 时与 `cogvideox` 相同，走 **`build_video_from_clips`** 拼 TTS 与字幕。
