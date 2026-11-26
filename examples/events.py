# SPDX-License-Identifier: Apache-2.0
"""Deliver one external event without a heartbeat interval or a model.

python examples/events.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from ai_life_engine import EngineConfig, LifeEngine


async def _run() -> None:
    default = Path(__file__).resolve().parent / ".runtime" / "events"
    data_dir = Path(os.environ.get("AI_LIFE_ENGINE_DATA_DIR", default))
    config = EngineConfig(data_dir=data_dir, name="events", event_poll_seconds=0.05)
    engine = LifeEngine(config)
    seen: list[str] = []
    engine.events.subscribe("ping", lambda event: seen.append(event.type))

    async def _publish() -> None:
        await asyncio.sleep(0.05)
        engine.events.publish("ping", source="example", payload={"from": "outside"})

    async def _stop() -> None:
        while not seen:
            await asyncio.sleep(0.01)
        engine.request_stop("example complete")

    try:
        await asyncio.gather(engine.run(), _publish(), _stop())
        print(f"delivered={len(seen)}")
        print(f"tick_count={engine.heartbeat.status().tick_count}")
        print("llm_configured=false")
    finally:
        engine.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
