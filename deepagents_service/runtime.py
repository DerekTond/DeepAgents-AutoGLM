"""Reusable non-CLI runtime for DeepAgents.

This module exposes `DeepAgentsRuntime`, an async runtime wrapper around the
existing DeepAgents agent construction logic. It is intended for service usage
without the Textual CLI.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from collections.abc import AsyncIterator
from typing import Any, Literal, TypedDict

from langchain.agents.middleware.human_in_the_loop import (
    ActionRequest,
    HITLRequest,
)
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command, Interrupt
from pydantic import TypeAdapter, ValidationError

from deepagents_cli.agent import DEFAULT_AGENT_NAME, create_cli_agent
from deepagents_cli.config import (
    SHELL_TOOL_NAMES,
    is_shell_command_allowed,
    parse_shell_allow_list,
    settings,
)
from deepagents_cli.integrations.sandbox_factory import create_sandbox
from deepagents_cli.sessions import generate_thread_id, get_checkpointer
from deepagents_cli.tools import fetch_url, http_request, web_search

logger = logging.getLogger(__name__)

_HITL_REQUEST_ADAPTER = TypeAdapter(HITLRequest)
_MAX_HITL_ITERATIONS = 50


class RuntimeEvent(TypedDict, total=False):
    """Structured event emitted by `DeepAgentsRuntime.astream_events`."""

    type: Literal["text", "tool_call", "interrupt", "done", "warning"]
    thread_id: str
    text: str
    tool_name: str
    tool_id: str
    interrupt_id: str
    decision: str
    reason: str
    output: str
    tool_calls: list[str]
    interrupts: int


@dataclass(frozen=True, slots=True)
class ChatRunResult:
    """Final result from a completed non-stream chat turn."""

    thread_id: str
    output: str
    tool_calls: list[str]
    interrupts: int


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Initialization config for `DeepAgentsRuntime`."""

    assistant_id: str = DEFAULT_AGENT_NAME
    model_name: str | None = None
    sandbox_type: str = "none"
    sandbox_id: str | None = None
    sandbox_setup: str | None = None
    shell_allow_list: list[str] | None = None
    auto_approve: bool = True
    enable_shell: bool = False


class DeepAgentsRuntime:
    """Service-friendly runtime for DeepAgents.

    The runtime owns an initialized agent graph and exposes:
    - `run(...)` for one-shot responses
    - `astream_events(...)` for incremental events suitable for WebSockets/SSE
    """

    def __init__(
        self,
        *,
        assistant_id: str = DEFAULT_AGENT_NAME,
        model_name: str | None = None,
        sandbox_type: str = "none",
        sandbox_id: str | None = None,
        sandbox_setup: str | None = None,
        shell_allow_list: str | list[str] | None = None,
        auto_approve: bool | None = None,
        enable_shell: bool | None = None,
    ) -> None:
        if isinstance(shell_allow_list, str):
            parsed_allow_list = parse_shell_allow_list(shell_allow_list)
        else:
            parsed_allow_list = shell_allow_list

        resolved_enable_shell = (
            enable_shell if enable_shell is not None else bool(parsed_allow_list)
        )
        resolved_auto_approve = (
            auto_approve if auto_approve is not None else not resolved_enable_shell
        )

        self.config = RuntimeConfig(
            assistant_id=assistant_id,
            model_name=model_name,
            sandbox_type=sandbox_type,
            sandbox_id=sandbox_id,
            sandbox_setup=sandbox_setup,
            shell_allow_list=parsed_allow_list,
            auto_approve=resolved_auto_approve,
            enable_shell=resolved_enable_shell,
        )

        self._agent: Any | None = None
        self._backend: Any | None = None
        self._sandbox_cm: contextlib.AbstractContextManager[Any] | None = None
        self._checkpointer_cm: Any | None = None
        self._checkpointer: Any | None = None

        self._start_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        """Initialize model, sandbox/checkpointer, and compiled agent."""
        if self._started:
            return

        async with self._start_lock:
            if self._started:
                return

            from deepagents_cli.config import create_model

            model = create_model(self.config.model_name)
            sandbox_backend = None

            if self.config.sandbox_type != "none":
                self._sandbox_cm = create_sandbox(
                    self.config.sandbox_type,
                    sandbox_id=self.config.sandbox_id,
                    setup_script_path=self.config.sandbox_setup,
                )
                sandbox_backend = self._sandbox_cm.__enter__()  # noqa: PLC2801

            self._checkpointer_cm = get_checkpointer()
            self._checkpointer = await self._checkpointer_cm.__aenter__()

            tools = [http_request, fetch_url]
            if settings.has_tavily:
                tools.append(web_search)

            self._agent, self._backend = create_cli_agent(
                model=model,
                assistant_id=self.config.assistant_id,
                tools=tools,
                sandbox=sandbox_backend,
                sandbox_type=(
                    self.config.sandbox_type
                    if self.config.sandbox_type != "none"
                    else None
                ),
                auto_approve=self.config.auto_approve,
                enable_shell=self.config.enable_shell,
                checkpointer=self._checkpointer,
            )
            self._started = True

    async def close(self) -> None:
        """Close runtime resources (checkpointer and optional sandbox)."""
        if self._checkpointer_cm is not None:
            with contextlib.suppress(Exception):
                await self._checkpointer_cm.__aexit__(None, None, None)
            self._checkpointer_cm = None
            self._checkpointer = None

        if self._sandbox_cm is not None:
            with contextlib.suppress(Exception):
                self._sandbox_cm.__exit__(None, None, None)
            self._sandbox_cm = None

        self._agent = None
        self._backend = None
        self._started = False

    async def __aenter__(self) -> "DeepAgentsRuntime":
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    def _build_stream_config(self, thread_id: str) -> RunnableConfig:
        return {
            "configurable": {"thread_id": thread_id},
            "metadata": {
                "assistant_id": self.config.assistant_id,
                "agent_name": self.config.assistant_id,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        }

    @staticmethod
    def _extract_text_chunks(message: AIMessage) -> list[str]:
        chunks: list[str] = []
        content_blocks = getattr(message, "content_blocks", None)
        if isinstance(content_blocks, list):
            for block in content_blocks:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                    and block["text"]
                ):
                    chunks.append(block["text"])
            if chunks:
                return chunks

        if isinstance(message.content, str) and message.content:
            chunks.append(message.content)
        return chunks

    @staticmethod
    def _extract_tool_calls(message: AIMessage) -> list[dict[str, str | None]]:
        tool_calls: list[dict[str, str | None]] = []

        raw_tool_calls = getattr(message, "tool_calls", None)
        if isinstance(raw_tool_calls, list):
            for call in raw_tool_calls:
                if isinstance(call, dict):
                    tool_calls.append(
                        {
                            "id": (
                                call.get("id")
                                if isinstance(call.get("id"), str)
                                else None
                            ),
                            "name": (
                                call.get("name")
                                if isinstance(call.get("name"), str)
                                else "unknown"
                            ),
                        }
                    )

        content_blocks = getattr(message, "content_blocks", None)
        if isinstance(content_blocks, list):
            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") not in {"tool_call", "tool_call_chunk"}:
                    continue
                name = block.get("name")
                if not isinstance(name, str) or not name:
                    continue
                tool_calls.append(
                    {
                        "id": block.get("id") if isinstance(block.get("id"), str) else None,
                        "name": name,
                    }
                )
        return tool_calls

    def _make_decision(
        self, action_request: ActionRequest
    ) -> tuple[dict[str, str], str, str]:
        action_name = action_request.get("name", "")

        if self.config.auto_approve:
            return {"type": "approve"}, "approve", "auto_approve enabled"

        if action_name in SHELL_TOOL_NAMES:
            command = str(action_request.get("args", {}).get("command", ""))
            if (
                self.config.shell_allow_list
                and is_shell_command_allowed(command, self.config.shell_allow_list)
            ):
                return {"type": "approve"}, "approve", "shell command allow-listed"
            return (
                {
                    "type": "reject",
                    "message": (
                        f"Command '{command}' is not allowed by shell allow-list."
                    ),
                },
                "reject",
                "shell command rejected by allow-list",
            )

        return {"type": "approve"}, "approve", "non-shell action"

    @staticmethod
    def _validate_interrupt(
        interrupt_obj: Interrupt,
    ) -> tuple[str, HITLRequest | None]:
        try:
            validated = _HITL_REQUEST_ADAPTER.validate_python(interrupt_obj.value)
        except ValidationError:
            return interrupt_obj.id, None
        return interrupt_obj.id, validated

    async def run(
        self,
        message: str,
        *,
        thread_id: str | None = None,
    ) -> ChatRunResult:
        """Execute one turn and return a fully aggregated response."""
        done_event: RuntimeEvent | None = None
        async for event in self.astream_events(message, thread_id=thread_id):
            if event.get("type") == "done":
                done_event = event

        if done_event is None:
            raise RuntimeError("Agent run completed without a done event.")

        return ChatRunResult(
            thread_id=str(done_event["thread_id"]),
            output=str(done_event.get("output", "")),
            tool_calls=list(done_event.get("tool_calls", [])),
            interrupts=int(done_event.get("interrupts", 0)),
        )

    async def astream_events(
        self,
        message: str,
        *,
        thread_id: str | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        """Stream structured runtime events for one chat turn."""
        if not message or not message.strip():
            raise ValueError("message cannot be empty")

        await self.start()
        if self._agent is None:
            raise RuntimeError("Runtime agent is not initialized.")

        async with self._run_lock:
            run_thread_id = thread_id or generate_thread_id()
            config = self._build_stream_config(run_thread_id)
            stream_input: dict[str, Any] | Command = {
                "messages": [{"role": "user", "content": message}]
            }

            seen_tool_ids: set[str] = set()
            output_parts: list[str] = []
            tool_call_names: list[str] = []
            interrupt_count = 0
            iterations = 0

            while True:
                pending_interrupts: dict[str, HITLRequest | None] = {}

                async for chunk in self._agent.astream(
                    stream_input,
                    stream_mode=["messages", "updates"],
                    subgraphs=True,
                    config=config,
                    durability="exit",
                ):
                    if not isinstance(chunk, tuple) or len(chunk) != 3:
                        continue

                    namespace, stream_mode, data = chunk
                    if namespace:
                        continue

                    if stream_mode == "messages":
                        if not isinstance(data, tuple) or len(data) != 2:
                            continue
                        message_obj, metadata = data
                        if (
                            isinstance(metadata, dict)
                            and metadata.get("lc_source") == "summarization"
                        ):
                            continue
                        if not isinstance(message_obj, AIMessage):
                            continue

                        for text_chunk in self._extract_text_chunks(message_obj):
                            output_parts.append(text_chunk)
                            yield RuntimeEvent(
                                type="text",
                                thread_id=run_thread_id,
                                text=text_chunk,
                            )

                        for tool_call in self._extract_tool_calls(message_obj):
                            tool_id = tool_call.get("id") or (
                                f"tool-{len(seen_tool_ids) + 1}"
                            )
                            if tool_id in seen_tool_ids:
                                continue
                            seen_tool_ids.add(tool_id)
                            tool_name = tool_call.get("name") or "unknown"
                            tool_call_names.append(tool_name)
                            yield RuntimeEvent(
                                type="tool_call",
                                thread_id=run_thread_id,
                                tool_id=tool_id,
                                tool_name=tool_name,
                            )

                    elif (
                        stream_mode == "updates"
                        and isinstance(data, dict)
                        and "__interrupt__" in data
                        and isinstance(data["__interrupt__"], list)
                    ):
                        for interrupt_obj in data["__interrupt__"]:
                            if not isinstance(interrupt_obj, Interrupt):
                                continue
                            interrupt_id, hitl_request = self._validate_interrupt(
                                interrupt_obj
                            )
                            pending_interrupts[interrupt_id] = hitl_request

                if not pending_interrupts:
                    break

                iterations += 1
                if iterations > _MAX_HITL_ITERATIONS:
                    msg = (
                        f"Exceeded {_MAX_HITL_ITERATIONS} HITL iterations. "
                        "The agent may be retrying rejected actions."
                    )
                    raise RuntimeError(msg)

                interrupt_count += len(pending_interrupts)
                resume_payload: dict[str, dict[str, list[dict[str, str]]]] = {}

                for interrupt_id, hitl_request in pending_interrupts.items():
                    decisions: list[dict[str, str]] = []
                    reasons: list[str] = []

                    if hitl_request is None:
                        decisions = [
                            {
                                "type": "reject",
                                "message": "Malformed interrupt payload.",
                            }
                        ]
                        reasons.append("malformed interrupt payload")
                    else:
                        for action_request in hitl_request["action_requests"]:
                            decision, _decision_type, reason = self._make_decision(
                                action_request
                            )
                            decisions.append(decision)
                            reasons.append(reason)

                    resume_payload[interrupt_id] = {"decisions": decisions}

                    for idx, decision in enumerate(decisions):
                        reason = reasons[idx] if idx < len(reasons) else ""
                        yield RuntimeEvent(
                            type="interrupt",
                            thread_id=run_thread_id,
                            interrupt_id=interrupt_id,
                            decision=decision.get("type", "reject"),
                            reason=reason,
                        )

                stream_input = Command(resume=resume_payload)

            yield RuntimeEvent(
                type="done",
                thread_id=run_thread_id,
                output="".join(output_parts),
                tool_calls=tool_call_names,
                interrupts=interrupt_count,
            )
