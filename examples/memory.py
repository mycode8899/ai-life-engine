# SPDX-License-Identifier: Apache-2.0
"""Store memory, restart, and read it back without a model.

python examples/memory.py
"""

from __future__ import annotations

import os
from pathlib import Path

from ai_life_engine import EngineConfig, LifeEngine


def main() -> None:
    default = Path(__file__).resolve().parent / ".runtime" / "memory"
    data_dir = Path(os.environ.get("AI_LIFE_ENGINE_DATA_DIR", default))
    config = EngineConfig(data_dir=data_dir, name="memory")
    writer = LifeEngine(config)
    try:
        writer.initialize()
        writer.memory.remember_working("task", "review the log")
        writer.memory.remember_episode("The runtime started without a model.")
        writer.memory.remember_fact("project", "AI Life Engine stores memory in SQLite.")
    finally:
        writer.close()

    reader = LifeEngine(config)
    try:
        working = reader.memory.recall_working("task")
        fact = reader.memory.recall_fact("project")
        hits = reader.memory.search("model")
        survived = working is not None and fact is not None and len(hits) == 1
        print(f"survived={str(survived).lower()}")
        print(f"tick_count={reader.heartbeat.status().tick_count}")
        print("llm_configured=false")
    finally:
        reader.close()


if __name__ == "__main__":
    main()
