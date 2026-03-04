"""Smoke test for the non-CLI WebSocket stream endpoint.

Run after `deepagents-service` is up:
    python examples/non_cli_ws_smoke.py

Optional environment variables:
    BASE_URL=http://127.0.0.1:8000
    WS_MESSAGE="Summarize the repository"
    THREAD_ID=smoke-thread-ws-001
"""

from __future__ import annotations

import asyncio
import json
import os

try:
    import websockets
except ImportError as exc:  # pragma: no cover - manual smoke test path
    raise SystemExit(
        "The `websockets` package is required. Install with: pip install websockets"
    ) from exc


async def main() -> None:
    base_url = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_url}/chat/stream"

    payload = {
        "message": os.environ.get("WS_MESSAGE", "Please summarize the project briefly."),
        "thread_id": os.environ.get("THREAD_ID", "smoke-thread-ws-001"),
    }

    print(f"Connecting to: {ws_url}")
    async with websockets.connect(ws_url, max_size=2**22) as ws:
        await ws.send(json.dumps(payload))
        print(f"Sent payload: {payload}")

        while True:
            raw = await ws.recv()
            event = json.loads(raw)
            print(event)

            if event.get("type") == "done":
                print("WebSocket smoke test finished.")
                return


if __name__ == "__main__":
    asyncio.run(main())

