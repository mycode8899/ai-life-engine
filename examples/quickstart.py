# SPDX-License-Identifier: Apache-2.0
"""Start AI Life Engine without configuring a language model.

python examples/quickstart.py
"""

from __future__ import annotations

import os
from pathlib import Path

from ai_life_engine import EngineConfig, LifeEngine


def main() -> None:
    default = Path(__file__).resolve().parent / ".runtime" / "quickstart"
    data_dir = Path(os.environ.get("AI_LIFE_ENGINE_DATA_DIR", default))
    config = EngineConfig(data_dir=data_dir, name="quickstart")
    with LifeEngine(config) as engine:
        snapshot = engine.status()
        report = engine.health()
        print(f"engine_id={snapshot.engine_id}")
        print(f"state={snapshot.state.value}")
        print(f"health={report.status.value}")
        print(f"llm_configured={str(engine.brain.configured).lower()}")


if __name__ == "__main__":
    main()
