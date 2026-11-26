# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from ai_life_engine import EngineConfig, LifecycleState, LifeEngine


def _env() -> dict[str, str]:
    env = os.environ.copy()
    src = Path(__file__).resolve().parents[2] / "src"
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(src) if not previous else str(src) + os.pathsep + previous
    env["PYTHONUNBUFFERED"] = "1"
    return env


def test_killed_process_recovers_on_the_next_start(tmp_path: Path) -> None:
    data = tmp_path / "crash"
    script = textwrap.dedent(
        """
        import asyncio
        import sys
        from pathlib import Path

        from ai_life_engine import EngineConfig, LifeEngine

        async def main() -> None:
            engine = LifeEngine(EngineConfig(data_dir=Path(sys.argv[1]), name="crash"))

            def announce(snapshot) -> None:
                print(f"running {snapshot.engine_id}", flush=True)

            await engine.run(install_signal_handlers=False, on_started=announce)

        asyncio.run(main())
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(data)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(),
    )
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert line.startswith("running "), proc.stderr.read() if proc.stderr else line
    proc.kill()
    proc.wait(timeout=5)

    deadline = time.time() + 5
    recovered = False
    last_error = "recovery was not required"
    while time.time() < deadline:
        engine = LifeEngine(EngineConfig(data_dir=data, name="crash"))
        try:
            snapshot = engine.status()
            if snapshot.recovery_required:
                started = engine.start()
                assert started.recovery_count == 1
                assert started.state is LifecycleState.RUNNING
                engine.stop()
                recovered = True
                break
            last_error = f"state={snapshot.persisted_state} pid={snapshot.pid}"
        finally:
            if engine.owns_runtime:
                engine.stop()
            engine.close()
        time.sleep(0.05)
    assert recovered, last_error
