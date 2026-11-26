# SPDX-License-Identifier: Apache-2.0
"""Show that a killed runtime is recovered on the next start.

python examples/recovery_after_restart.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

from ai_life_engine import EngineConfig, LifecycleState, LifeEngine, RuntimeSnapshot
from ai_life_engine.exceptions import LifeEngineError


def _data_dir(argument: Path | None) -> Path:
    if argument is not None:
        return argument
    env = os.environ.get("AI_LIFE_ENGINE_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / ".runtime" / "recovery"


def child(data_dir: Path) -> int:
    config = EngineConfig(data_dir=data_dir, name="recovery-demo")

    async def _main() -> None:
        engine = LifeEngine(config)

        def announce(snapshot: RuntimeSnapshot) -> None:
            print(f"child running {snapshot.engine_id}", flush=True)

        await engine.run(install_signal_handlers=False, on_started=announce)

    asyncio.run(_main())
    return 0


def parent(data_dir: Path) -> int:
    script = str(Path(__file__).resolve())
    proc = subprocess.Popen([sys.executable, script, "--child", "--data-dir", str(data_dir)])
    try:
        deadline = time.time() + 20
        running = False
        while time.time() < deadline:
            try:
                engine = LifeEngine(EngineConfig(data_dir=data_dir))
            except LifeEngineError:
                time.sleep(0.05)
                continue
            try:
                if engine.status().persisted_state is LifecycleState.RUNNING:
                    running = True
                    break
            finally:
                engine.close()
            time.sleep(0.05)
        if not running:
            print("child did not start", file=sys.stderr)
            return 1
        proc.kill()
        proc.wait(timeout=5)
        engine = LifeEngine(EngineConfig(data_dir=data_dir))
        try:
            if not engine.status().recovery_required:
                print("expected a runtime that needs recovery", file=sys.stderr)
                return 1
            engine.start()
            count = engine.status().recovery_count
            print(f"recovered recovery_count={count}")
            engine.stop()
        finally:
            if engine.owns_runtime:
                engine.stop()
            engine.close()
        return 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    data_dir = _data_dir(args.data_dir)
    if args.child:
        return child(data_dir)
    return parent(data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
