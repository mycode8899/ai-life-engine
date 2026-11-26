# SPDX-License-Identifier: Apache-2.0
"""Select a local model provider without changing the runtime.

python examples/brain.py
"""

from __future__ import annotations

import os
from pathlib import Path

from ai_life_engine import EchoProvider, EngineConfig, LifeEngine, open_model


def main() -> None:
    default = Path(__file__).resolve().parent / ".runtime" / "brain"
    data_dir = Path(os.environ.get("AI_LIFE_ENGINE_DATA_DIR", default))
    config = EngineConfig(data_dir=data_dir, name="brain")
    engine = LifeEngine(config)
    try:
        engine.initialize()
        engine.brain.use(open_model("echo"))
        first = engine.brain.complete("ping")
        engine.brain.use(EchoProvider())
        second = engine.brain.complete("pong")
        print(f"provider={second.provider}")
        print(f"first={first.text}")
        print(f"second={second.text}")
        print(f"requests={engine.brain.totals().requests}")
        print(f"tick_count={engine.heartbeat.status().tick_count}")
    finally:
        engine.close()


if __name__ == "__main__":
    main()
