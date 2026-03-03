"""FastAPI service wrapper for `DeepAgentsRuntime`."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from deepagents_cli.agent import DEFAULT_AGENT_NAME
from deepagents_service.runtime import ChatRunResult, DeepAgentsRuntime


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """Configuration for the non-CLI HTTP service."""

    assistant_id: str
    model_name: str | None
    sandbox_type: str
    sandbox_id: str | None
    sandbox_setup: str | None
    shell_allow_list: str | None
    auto_approve: bool | None


def load_service_config() -> ServiceConfig:
    """Load service settings from environment variables."""
    return ServiceConfig(
        assistant_id=os.environ.get(
            "DEEPAGENTS_SERVICE_ASSISTANT_ID", DEFAULT_AGENT_NAME
        ),
        model_name=os.environ.get("DEEPAGENTS_SERVICE_MODEL"),
        sandbox_type=os.environ.get("DEEPAGENTS_SERVICE_SANDBOX", "none"),
        sandbox_id=os.environ.get("DEEPAGENTS_SERVICE_SANDBOX_ID"),
        sandbox_setup=os.environ.get("DEEPAGENTS_SERVICE_SANDBOX_SETUP"),
        shell_allow_list=os.environ.get("DEEPAGENTS_SERVICE_SHELL_ALLOW_LIST"),
        auto_approve=_parse_bool(os.environ.get("DEEPAGENTS_SERVICE_AUTO_APPROVE")),
    )


class RuntimeManager:
    """Own a lazily initialized runtime singleton for the app process."""

    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._runtime: DeepAgentsRuntime | None = None
        self._lock = asyncio.Lock()

    async def get_runtime(self) -> DeepAgentsRuntime:
        if self._runtime is not None:
            return self._runtime

        async with self._lock:
            if self._runtime is None:
                runtime = DeepAgentsRuntime(
                    assistant_id=self._config.assistant_id,
                    model_name=self._config.model_name,
                    sandbox_type=self._config.sandbox_type,
                    sandbox_id=self._config.sandbox_id,
                    sandbox_setup=self._config.sandbox_setup,
                    shell_allow_list=self._config.shell_allow_list,
                    auto_approve=self._config.auto_approve,
                )
                await runtime.start()
                self._runtime = runtime
        return self._runtime

    async def close(self) -> None:
        if self._runtime is not None:
            await self._runtime.close()
            self._runtime = None


class ChatRequest(BaseModel):
    """Single chat request payload."""

    message: str = Field(..., min_length=1, description="User input message")
    thread_id: str | None = Field(
        default=None,
        description="Optional existing thread ID for a continued conversation",
    )


class ChatResponse(BaseModel):
    """Non-stream chat response payload."""

    thread_id: str
    output: str
    tool_calls: list[str]
    interrupts: int


def create_app() -> FastAPI:
    """Application factory for ASGI servers."""
    try:
        from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    except ImportError as exc:
        msg = (
            "FastAPI is required to run the HTTP service. "
            "Install with: pip install -e '.[service]'"
        )
        raise RuntimeError(msg) from exc

    config = load_service_config()
    runtime_manager = RuntimeManager(config)

    app = FastAPI(
        title="DeepAgents Service",
        version="0.1.0",
        description="Non-CLI DeepAgents runtime exposed over HTTP/WebSocket.",
    )
    app.state.runtime_manager = runtime_manager

    @app.on_event("startup")
    async def _startup() -> None:
        await runtime_manager.get_runtime()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await runtime_manager.close()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        runtime = await runtime_manager.get_runtime()
        try:
            result: ChatRunResult = await runtime.run(
                request.message, thread_id=request.thread_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return ChatResponse(
            thread_id=result.thread_id,
            output=result.output,
            tool_calls=result.tool_calls,
            interrupts=result.interrupts,
        )

    @app.websocket("/chat/stream")
    async def chat_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        runtime = await runtime_manager.get_runtime()

        try:
            payload: Any = await websocket.receive_json()
            request = ChatRequest.model_validate(payload)
        except ValidationError as exc:
            await websocket.send_json(
                {"type": "error", "message": "Invalid payload", "detail": exc.errors()}
            )
            await websocket.close(code=1003)
            return
        except Exception:  # noqa: BLE001
            await websocket.close(code=1003)
            return

        try:
            async for event in runtime.astream_events(
                request.message, thread_id=request.thread_id
            ):
                await websocket.send_json(event)
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close(code=1011)
            return

        await websocket.close()

    return app


def main() -> None:
    """Run the service with Uvicorn."""
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - runtime guidance path
        msg = (
            "Uvicorn is required to run the HTTP service. "
            "Install with: pip install -e '.[service]'"
        )
        raise SystemExit(msg) from exc

    host = os.environ.get("DEEPAGENTS_SERVICE_HOST", "0.0.0.0")
    port = int(os.environ.get("DEEPAGENTS_SERVICE_PORT", "8000"))

    uvicorn.run(
        "deepagents_service.server:create_app",
        factory=True,
        host=host,
        port=port,
    )


__all__ = [
    "create_app",
    "main",
]
