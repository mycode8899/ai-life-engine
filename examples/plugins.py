# SPDX-License-Identifier: Apache-2.0
"""Install one local plugin and show that its tool is still denied.

python examples/plugins.py

The plugin registers a tool. The application does not grant it. The heartbeat
is not running.
"""

from __future__ import annotations

import os
from pathlib import Path

from ai_life_engine import AgentError, EngineConfig, LifeEngine


class _Ping:
    name = "ping"
    description = "Return pong."
    permission = "ping"

    def call(self, argument: str) -> str:
        del argument
        return "pong"


class _Sample:
    name = "sample"

    def setup(self, context: object) -> None:
        context.register_tool(_Ping())


def main() -> None:
    default = Path(__file__).resolve().parent / ".runtime" / "plugins"
    data_dir = Path(os.environ.get("AI_LIFE_ENGINE_DATA_DIR", default))
    engine = LifeEngine(EngineConfig(data_dir=data_dir, name="plugins"))
    try:
        record = engine.plugins.install(_Sample())
        engine.initialize()
        denied = False
        try:
            engine.agents.call("ping", "now")
        except AgentError as exc:
            denied = exc.code == "denied"
        print(f"plugin={record.name}")
        print(f"status={record.status.value}")
        print(f"denied={str(denied).lower()}")
        print(f"tick_count={engine.heartbeat.status().tick_count}")
    finally:
        engine.close()


if __name__ == "__main__":
    main()
