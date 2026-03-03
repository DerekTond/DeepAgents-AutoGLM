"""Minimal SDK-style usage example for `DeepAgentsRuntime`."""

from __future__ import annotations

import asyncio

from deepagents_service.runtime import DeepAgentsRuntime


async def main() -> None:
    async with DeepAgentsRuntime() as runtime:
        result = await runtime.run("Summarize the repository structure in 5 bullets.")
        print(f"thread_id={result.thread_id}")
        print(result.output)


if __name__ == "__main__":
    asyncio.run(main())

