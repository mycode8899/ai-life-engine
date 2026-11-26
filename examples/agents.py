# SPDX-License-Identifier: Apache-2.0
"""Grant a tool, ask an adapter to echo the next action, then finish it.

python examples/agents.py

The echo tool returns text. finish records the step as succeeded. Neither
opens a shell, and the heartbeat is not running.
"""

from __future__ import annotations

import os
from pathlib import Path

from ai_life_engine import EchoAgent, EngineConfig, LifeEngine


def main() -> None:
    default = Path(__file__).resolve().parent / ".runtime" / "agents"
    data_dir = Path(os.environ.get("AI_LIFE_ENGINE_DATA_DIR", default))
    config = EngineConfig(data_dir=data_dir, name="agents")
    engine = LifeEngine(config)
    try:
        engine.initialize()
        engine.agents.allow("echo")
        engine.agents.allow("goals.write")
        goal = engine.goals.add("Write notes", "Keep a durable next step.", priority=1)
        engine.goals.plan(goal.goal_id, ["Draft the notes"])
        engine.agents.use(EchoAgent())
        step = engine.agents.step()
        finished = engine.agents.call("finish", "Drafted the notes.")
        print(f"tool={step.tool}")
        print(f"result={step.result}")
        print(f"finish={finished}")
        print(f"next={'none' if engine.goals.next_action() is None else 'waiting'}")
        print(f"tick_count={engine.heartbeat.status().tick_count}")
    finally:
        engine.close()


if __name__ == "__main__":
    main()
