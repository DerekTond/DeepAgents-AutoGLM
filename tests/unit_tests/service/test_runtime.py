"""Unit tests for the non-CLI runtime."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Interrupt

from deepagents_service.runtime import DeepAgentsRuntime


class FakeAgent:
    """Deterministic fake stream source for runtime tests."""

    def __init__(self, chunk_batches: Sequence[list[tuple[Any, Any, Any]]]) -> None:
        self._chunk_batches = list(chunk_batches)
        self.calls = 0

    async def astream(self, *_args: Any, **_kwargs: Any):  # noqa: ANN401
        if self.calls >= len(self._chunk_batches):
            return
        batch = self._chunk_batches[self.calls]
        self.calls += 1
        for chunk in batch:
            yield chunk


@pytest.mark.asyncio
async def test_astream_events_emits_text_tool_and_done_events() -> None:
    """Runtime should stream text/tool events and a final done event."""
    fake_agent = FakeAgent(
        [
            [
                ((), "messages", (AIMessage(content="Hello "), {})),
                ((), "messages", (AIMessage(content="World"), {})),
                (
                    (),
                    "messages",
                    (
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": "call_1",
                                    "name": "fetch_url",
                                    "args": {"url": "https://example.com"},
                                }
                            ],
                        ),
                        {},
                    ),
                ),
            ]
        ]
    )

    runtime = DeepAgentsRuntime()
    runtime._started = True  # bypass external model/tool setup
    runtime._agent = fake_agent

    events = [
        event
        async for event in runtime.astream_events("hello", thread_id="thread-abc123")
    ]

    assert [e["type"] for e in events] == ["text", "text", "tool_call", "done"]
    assert events[0]["text"] == "Hello "
    assert events[1]["text"] == "World"
    assert events[2]["tool_name"] == "fetch_url"
    assert events[3]["thread_id"] == "thread-abc123"
    assert events[3]["output"] == "Hello World"
    assert events[3]["tool_calls"] == ["fetch_url"]
    assert events[3]["interrupts"] == 0


@pytest.mark.asyncio
async def test_astream_events_rejects_shell_interrupt_not_in_allow_list() -> None:
    """Shell commands outside the allow-list should be rejected."""
    hitl_request = {
        "action_requests": [
            {"name": "execute", "args": {"command": "rm -rf /tmp/x"}}
        ],
        "review_configs": [
            {"action_name": "execute", "allowed_decisions": ["approve", "reject"]}
        ],
    }

    fake_agent = FakeAgent(
        [
            [
                (
                    (),
                    "updates",
                    {"__interrupt__": [Interrupt(value=hitl_request, id="int-1")]},
                )
            ],
            [
                ((), "messages", (AIMessage(content="blocked"), {})),
            ],
        ]
    )

    runtime = DeepAgentsRuntime(
        auto_approve=False,
        shell_allow_list=["ls", "cat"],
        enable_shell=True,
    )
    runtime._started = True
    runtime._agent = fake_agent

    events = [
        event async for event in runtime.astream_events("hi", thread_id="thread-reject")
    ]

    interrupt_events = [e for e in events if e["type"] == "interrupt"]
    assert len(interrupt_events) == 1
    assert interrupt_events[0]["interrupt_id"] == "int-1"
    assert interrupt_events[0]["decision"] == "reject"
    assert "allow-list" in interrupt_events[0]["reason"]

    done_event = events[-1]
    assert done_event["type"] == "done"
    assert done_event["interrupts"] == 1


@pytest.mark.asyncio
async def test_run_returns_aggregated_result() -> None:
    """run() should aggregate final content from streamed events."""
    fake_agent = FakeAgent(
        [
            [
                ((), "messages", (AIMessage(content="line1"), {})),
                ((), "messages", (AIMessage(content=" line2"), {})),
            ]
        ]
    )
    runtime = DeepAgentsRuntime()
    runtime._started = True
    runtime._agent = fake_agent

    result = await runtime.run("hello", thread_id="thread-run-1")

    assert result.thread_id == "thread-run-1"
    assert result.output == "line1 line2"
    assert result.tool_calls == []
    assert result.interrupts == 0

