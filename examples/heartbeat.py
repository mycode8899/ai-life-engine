# SPDX-License-Identifier: Apache-2.0
"""Tick a few times without calling a language model.

python examples/heartbeat.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from ai_life_engine import EngineConfig, LifeEngine


async def _run() -> None:
    default = Path(__file__).resolve().parent / ".runtime" / "heartbeat"
    data_dir = Path(os.environ.get("AI_LIFE_ENGINE_DATA_DIR", default))
    interval = float(os.environ.get("AI_LIFE_ENGINE_HEARTBEAT_INTERVAL", "0.05"))
    ticks = int(os.environ.get("AI_LIFE_ENGINE_HEARTBEAT_TICKS", "3"))
    config = EngineConfig(
        data_dir=data_dir,
        name="heartbeat",
        heartbeat_interval=interval,
    )
    engine = LifeEngine(config)

    async def _stop_after() -> None:
        while engine.heartbeat.status().tick_count < ticks:
            await asyncio.sleep(0.01)
        engine.request_stop("example complete")

    try:
        await asyncio.gather(engine.run(), _stop_after())
        status = engine.heartbeat.status()
        print(f"tick_count={status.tick_count}")
        print(f"reasoning_count={status.reasoning_count}")
        print(f"llm_configured={str(engine.brain.configured).lower()}")
    finally:
        engine.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
