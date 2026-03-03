# DeepAgents 非 CLI 服务文档（架构 + 运行 + 测试）

本文档描述 `deepagents_service` 的设计、运行方式、接口约定和测试方案，适用于将 DeepAgents 作为后端能力集成到业务系统中。

## 1. 目标与范围

`deepagents_service` 提供两种非 CLI 使用方式：

1. **Python SDK**：直接在 Python 业务代码中调用运行时。
2. **HTTP 服务**：通过 FastAPI 暴露接口（REST + WebSocket）。

核心设计原则：

- 复用现有 `deepagents_cli` 的 Agent 构建与工具链能力，避免重复实现。
- 非 UI 化，不依赖 Textual 交互。
- 可串行处理会话，确保状态一致。

## 2. 架构总览

代码位置：

- `deepagents_service/runtime.py`
- `deepagents_service/server.py`

逻辑分层：

1. **Runtime 层**
   - `DeepAgentsRuntime` 负责 Agent 生命周期（start/close）和一次会话执行（run/astream_events）。
   - 复用 `create_cli_agent`、`get_checkpointer`、`create_sandbox`。
2. **Service 层**
   - `FastAPI` 封装 HTTP/WebSocket。
   - `RuntimeManager` 管理进程级 runtime 单例与懒加载。
3. **业务调用层**
   - 外部服务通过 REST 或 WebSocket 调用。
   - 或在 Python 中直接 `async with DeepAgentsRuntime()`.

## 3. 运行时生命周期

### 3.1 初始化（`start`）

`DeepAgentsRuntime.start()` 执行顺序：

1. 构造模型：`deepagents_cli.config.create_model`
2. 可选创建沙箱：`create_sandbox(...)`
3. 初始化 checkpointer：`get_checkpointer()`
4. 构建 agent：`create_cli_agent(...)`

### 3.2 执行（`astream_events` / `run`）

`astream_events(message, thread_id)` 以流形式返回事件：

- `text`：模型文本增量
- `tool_call`：工具调用
- `interrupt`：HITL 决策结果
- `done`：执行结束汇总

`run(...)` 在内部消费 `astream_events` 并返回最终聚合结果 `ChatRunResult`。

### 3.3 关闭（`close`）

释放 checkpointer 与沙箱上下文，避免泄漏资源。

## 4. 工具审批（HITL）策略

默认策略与 CLI 非交互模式一致：

- 未配置 shell allow-list 时：
  - shell 默认禁用（或被拒绝）
  - 其他工具自动批准
- 配置了 allow-list 后：
  - 只允许白名单 shell 命令
  - 非白名单命令会产生 `interrupt` 事件并返回 reject

相关参数：

- `shell_allow_list`
- `auto_approve`
- `enable_shell`

## 5. HTTP 接口约定

### 5.1 `GET /health`

响应：

```json
{"status": "ok"}
```

### 5.2 `POST /chat`

请求：

```json
{
  "message": "总结当前仓库结构",
  "thread_id": "optional-thread-id"
}
```

响应：

```json
{
  "thread_id": "thread-xxxx",
  "output": "最终回答文本",
  "tool_calls": ["fetch_url", "web_search"],
  "interrupts": 0
}
```

### 5.3 `WS /chat/stream`

连接后先发送一次 JSON 请求体（结构同 `/chat`），随后服务连续推送事件。

示例事件：

```json
{"type":"text","thread_id":"thread-1","text":"正在分析..."}
{"type":"tool_call","thread_id":"thread-1","tool_name":"fetch_url","tool_id":"call_1"}
{"type":"done","thread_id":"thread-1","output":"最终结果","tool_calls":["fetch_url"],"interrupts":0}
```

## 6. 运行说明

### 6.1 安装依赖

```bash
pip install -e ".[service]"
```

### 6.2 启动服务

```bash
deepagents-service
```

或：

```bash
python -m deepagents_service
```

### 6.3 常用环境变量

- `DEEPAGENTS_SERVICE_HOST`（默认 `0.0.0.0`）
- `DEEPAGENTS_SERVICE_PORT`（默认 `8000`）
- `DEEPAGENTS_SERVICE_MODEL`
- `DEEPAGENTS_SERVICE_ASSISTANT_ID`
- `DEEPAGENTS_SERVICE_SANDBOX`
- `DEEPAGENTS_SERVICE_SHELL_ALLOW_LIST`
- `DEEPAGENTS_SERVICE_AUTO_APPROVE`

## 7. Python SDK 运行示例

```python
import asyncio
from deepagents_service.runtime import DeepAgentsRuntime


async def main():
    async with DeepAgentsRuntime() as runtime:
        result = await runtime.run("列出项目重点模块")
        print(result.thread_id)
        print(result.output)


asyncio.run(main())
```

## 8. 常见报错与排查

### 8.1 `Uvicorn is required ...`

说明：未安装 service 依赖。  
处理：`pip install -e ".[service]"`

### 8.2 `FastAPI is required ...`

说明：直接调用了 service 接口但缺少 FastAPI。  
处理：`pip install -e ".[service]"`

### 8.3 `No credentials configured`

说明：未配置模型 API Key。  
处理：按 `.env.example` 配置 `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` 等。

### 8.4 shell 命令被拒绝

说明：命令不在 allow-list 中。  
处理：设置 `DEEPAGENTS_SERVICE_SHELL_ALLOW_LIST`，例如：

```bash
export DEEPAGENTS_SERVICE_SHELL_ALLOW_LIST="recommended,ls,cat"
```

## 9. TDD 测试说明

新增测试目录：

- `tests/unit_tests/service/test_runtime.py`
- `tests/unit_tests/service/test_server.py`

覆盖点：

- runtime 事件流（text/tool_call/done）
- interrupt 决策与 shell 拒绝策略
- run 聚合结果
- HTTP `/health`、`/chat`
- WebSocket `/chat/stream` 事件顺序

执行方式：

```bash
python3 -m pytest tests/unit_tests/service -q
```

