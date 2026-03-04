# DeepAgents Non-CLI Service Guide (Architecture + Runtime + Testing)

This guide documents the non-CLI runtime and HTTP service:

- `deepagents_service/runtime.py`
- `deepagents_service/server.py`

It is intended for embedding DeepAgents into backend systems.

## Architecture

The service has 3 layers:

1. Runtime layer (`DeepAgentsRuntime`)
   - Creates model, sandbox (optional), checkpointer, and agent graph.
   - Exposes `run(...)` and `astream_events(...)`.
2. Service layer (FastAPI app)
   - `RuntimeManager` owns a lazy singleton runtime.
   - Exposes REST and WebSocket endpoints.
3. Integration layer
   - Your app calls HTTP endpoints or uses Python SDK directly.

## Runtime Lifecycle

1. `start()`
   - create model
   - optional sandbox
   - open checkpointer
   - build agent
2. `run(...)` or `astream_events(...)`
   - execute one turn
   - process HITL interrupts and approval/rejection
3. `close()`
   - close checkpointer and sandbox resources

## API Endpoints

- `GET /health` -> `{"status":"ok"}`
- `POST /chat` -> final response payload
- `WS /chat/stream` -> event stream (`text`, `tool_call`, `interrupt`, `done`)

## Install and Run

```bash
pip install -e ".[service]"
deepagents-service
```

or:

```bash
python -m deepagents_service
```

## Environment Variables

- `DEEPAGENTS_SERVICE_HOST`
- `DEEPAGENTS_SERVICE_PORT`
- `DEEPAGENTS_SERVICE_MODEL`
- `DEEPAGENTS_SERVICE_ASSISTANT_ID`
- `DEEPAGENTS_SERVICE_SANDBOX`
- `DEEPAGENTS_SERVICE_SHELL_ALLOW_LIST`
- `DEEPAGENTS_SERVICE_AUTO_APPROVE`

## Python SDK Example

```python
import asyncio
from deepagents_service.runtime import DeepAgentsRuntime


async def main():
    async with DeepAgentsRuntime() as runtime:
        result = await runtime.run("Summarize repository architecture.")
        print(result.thread_id, result.output)


asyncio.run(main())
```

## Troubleshooting

- `Uvicorn is required ...` -> install `.[service]`
- `FastAPI is required ...` -> install `.[service]`
- `No credentials configured` -> set model API keys in `.env`
- shell action rejected -> update `DEEPAGENTS_SERVICE_SHELL_ALLOW_LIST`

## TDD Tests

New tests:

- `tests/unit_tests/service/test_runtime.py`
- `tests/unit_tests/service/test_server.py`

Run:

```bash
python3 -m pytest tests/unit_tests/service -q
```

## Smoke Test Scripts

The repository includes two quick smoke scripts:

- HTTP: `examples/non_cli_http_smoke.sh`
- WebSocket: `examples/non_cli_ws_smoke.py`

### HTTP smoke test

```bash
bash examples/non_cli_http_smoke.sh
```

With custom params:

```bash
BASE_URL=http://127.0.0.1:8000 \
MESSAGE="Summarize this project" \
THREAD_ID=demo-thread-001 \
bash examples/non_cli_http_smoke.sh
```

### WebSocket smoke test

```bash
python examples/non_cli_ws_smoke.py
```

With custom params:

```bash
BASE_URL=http://127.0.0.1:8000 \
WS_MESSAGE="Give me 3 highlights" \
THREAD_ID=demo-thread-ws-001 \
python examples/non_cli_ws_smoke.py
```
