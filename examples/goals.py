# SPDX-License-Identifier: Apache-2.0
"""Store a goal, plan two steps, and advance the next action.

python examples/goals.py

The next action is printed. It is not executed, and no model is called.
"""

from __future__ import annotations

import os
from pathlib import Path

from ai_life_engine import EngineConfig, LifeEngine


def main() -> None:
    default = Path(__file__).resolve().parent / ".runtime" / "goals"
    data_dir = Path(os.environ.get("AI_LIFE_ENGINE_DATA_DIR", default))
    config = EngineConfig(data_dir=data_dir, name="goals")
    engine = LifeEngine(config)
    try:
        engine.initialize()
        goal = engine.goals.add("Write notes", "Keep a durable next step.", priority=1)
        engine.goals.plan(goal.goal_id, ["Draft the notes", "Revise the notes"])
        first = engine.goals.next_action()
        assert first is not None
        engine.goals.reflect(goal.goal_id, outcome="succeeded", note="Drafted the notes.")
        second = engine.goals.next_action()
        assert second is not None
        print(f"goal_id={goal.goal_id}")
        print(f"first={first.action}")
        print(f"second={second.action}")
        print(f"status={engine.goals.get(goal.goal_id).status.value}")
        print(f"tick_count={engine.heartbeat.status().tick_count}")
        print(f"llm_configured={str(engine.brain.configured).lower()}")
    finally:
        engine.close()


if __name__ == "__main__":
    main()
