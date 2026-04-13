# Mamba-2 本地部署（对接本项目剧本 `SCRIPT_LLM_MODE=mamba`）

本项目的剧本生成通过 **OpenAI 兼容** 的 `Chat Completions` 调用后端（见 `app/services/script_service.py` 中 `_invoke_mamba_llm`）。本地部署 Mamba-2 系模型时，只要提供 **`http://<主机>:<端口>/v1`** 与 **`MAMBA_MODEL`（与服务端注册的模型名一致）** 即可。

## 1. 推荐：vLLM 提供 OpenAI 兼容服务

[vLLM](https://docs.vllm.ai/) 内置 Mamba2 相关实现，并自带 **OpenAI 兼容 HTTP API**（`/v1/chat/completions`），与本项目 LangChain `ChatOpenAI` 一致。

### 环境

- NVIDIA GPU + 足够显存（依所选模型参数量而定）
- Python 3.10+，安装与 CUDA 匹配的 PyTorch 后：

```bash
pip install vllm
```

### 启动服务（示例）

**`MAMBA_MODEL` 必须与下面 `vllm serve` 的第一个参数（Hugging Face 模型 ID 或本地路径）完全一致。**

```bash
# 示例 A：Hugging Face 上的 Mamba2 系模型（名称以你实际可用的 checkpoint 为准）
vllm serve mistralai/Mamba-Codestral-7B-v0.1 \
  --host 0.0.0.0 \
  --port 8000 \
  --dtype auto \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.9
```

若需鉴权（可选）：

```bash
vllm serve mistralai/Mamba-Codestral-7B-v0.1 \
  --host 0.0.0.0 \
  --port 8000 \
  --api-key your-secret-token
```

默认 OpenAI 兼容根地址为：**`http://127.0.0.1:8000/v1`**（注意末尾 **`/v1`**，与本项目 `openai_keys` / LangChain 用法一致）。

> **说明**：不同 vLLM 版本支持的模型列表会变化；若某 HF 模型启动报错，请查阅当前 vLLM 文档中的 [Mamba2](https://docs.vllm.ai/en/stable/api/vllm/model_executor/models/mamba2/) 与 [OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)，或换用文档中已验证的 checkpoint。

### Docker（可选）

```bash
docker run --runtime nvidia --gpus all \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -p 8000:8000 --ipc=host \
  vllm/vllm-openai:latest \
  --model mistralai/Mamba-Codestral-7B-v0.1 \
  --max-model-len 32768
```

## 2. 本项目 `.env` 配置

在工程根目录 `.env` 中设置（**不要**把真实 Key 提交到 Git）：

```env
SCRIPT_LLM_MODE=mamba
MAMBA_BASE_URL=http://127.0.0.1:8000/v1
MAMBA_MODEL=mistralai/Mamba-Codestral-7B-v0.1
# 若 vLLM 使用了 --api-key：
# MAMBA_API_KEY=your-secret-token
# 长剧本可适当增大（受模型与 vLLM --max-model-len 限制）
MAMBA_MAX_OUTPUT_TOKENS=8192
MAMBA_TIMEOUT_SEC=300
```

启动本应用后，剧本接口会请求 `MAMBA_BASE_URL` 上的 `chat/completions`，`model` 字段为 `MAMBA_MODEL`。

## 3. 自检

在终端用 `curl` 验证 vLLM 已就绪（将 `MODEL` 换成你的 `MAMBA_MODEL`）：

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mistralai/Mamba-Codestral-7B-v0.1","messages":[{"role":"user","content":"hi"}],"max_tokens":32}'
```

返回 JSON 中含 `choices` 即表示可与本项目联调。

## 4. 其它本地方案（非 vLLM）

若你使用 **Ollama、LM Studio、LocalAI** 等同样提供 **OpenAI 兼容 `/v1`** 的网关，只要：

- `MAMBA_BASE_URL` 指向其 **`.../v1`** 根路径；
- `MAMBA_MODEL` 与网关里显示的模型名一致；

即可同样使用 `SCRIPT_LLM_MODE=mamba`。具体安装步骤以对应工具文档为准。

## 5. 与「Zamba2」等混合架构的区别

社区中常见 **Zyphra/Zamba2-***（Mamba2 + Attention 混合）等模型，是否被当前 vLLM 版本直接支持以 **vLLM 启动是否成功** 为准。若仅能在 Transformers 下推理而无 OpenAI API，需自行包一层兼容网关，或改用已支持 `vllm serve` 的模型 ID。
