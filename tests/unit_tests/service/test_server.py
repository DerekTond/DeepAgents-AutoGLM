"""Unit tests for service config and runtime manager behavior."""

from __future__ import annotations

from typing import Any

import pytest

from deepagents_service.server import (
    RuntimeManager,
    ServiceConfig,
    _parse_bool,
    create_app,
    load_service_config,
)


def test_parse_bool_values() -> None:
    """Boolean parser should handle truthy/falsy variants."""
    assert _parse_bool("true") is True
    assert _parse_bool("YES") is True
    assert _parse_bool("0") is False
    assert _parse_bool("off") is False
    assert _parse_bool("maybe") is None
    assert _parse_bool(None) is None


def test_load_service_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config loader should map service env vars correctly."""
    monkeypatch.setenv("DEEPAGENTS_SERVICE_ASSISTANT_ID", "svc-agent")
    monkeypatch.setenv("DEEPAGENTS_SERVICE_MODEL", "gpt-5")
    monkeypatch.setenv("DEEPAGENTS_SERVICE_SANDBOX", "none")
    monkeypatch.setenv("DEEPAGENTS_SERVICE_SHELL_ALLOW_LIST", "recommended,ls")
    monkeypatch.setenv("DEEPAGENTS_SERVICE_AUTO_APPROVE", "false")

    cfg = load_service_config()

    assert cfg.assistant_id == "svc-agent"
    assert cfg.model_name == "gpt-5"
    assert cfg.sandbox_type == "none"
    assert cfg.shell_allow_list == "recommended,ls"
    assert cfg.auto_approve is False


@pytest.mark.asyncio
async def test_runtime_manager_lazy_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime manager should create runtime once and reuse it."""

    class DummyRuntime:
        instances = 0

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.started = False
            self.closed = False
            DummyRuntime.instances += 1

        async def start(self) -> None:
            self.started = True

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("deepagents_service.server.DeepAgentsRuntime", DummyRuntime)

    manager = RuntimeManager(
        ServiceConfig(
            assistant_id="svc-agent",
            model_name="gpt-5",
            sandbox_type="none",
            sandbox_id=None,
            sandbox_setup=None,
            shell_allow_list=None,
            auto_approve=True,
        )
    )

    runtime1 = await manager.get_runtime()
    runtime2 = await manager.get_runtime()

    assert runtime1 is runtime2
    assert runtime1.started is True
    assert DummyRuntime.instances == 1

    await manager.close()
    assert runtime1.closed is True


def test_create_app_registers_expected_routes() -> None:
    """App factory should register the non-CLI HTTP endpoints."""
    app = create_app()
    route_paths = {route.path for route in app.routes}

    assert "/health" in route_paths
    assert "/chat" in route_paths
    assert "/chat/stream" in route_paths

