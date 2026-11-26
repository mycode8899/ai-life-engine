# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _env(data_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    src = Path(__file__).resolve().parents[2] / "src"
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(src) if not previous else str(src) + os.pathsep + previous
    env["PYTHONUNBUFFERED"] = "1"
    env["AI_LIFE_ENGINE_DATA_DIR"] = str(data_dir)
    return env


def test_quickstart_example(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "examples" / "quickstart.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=_env(tmp_path / "quickstart"),
    )
    assert result.returncode == 0, result.stderr
    assert "state=running" in result.stdout
    assert "health=healthy" in result.stdout
    assert "llm_configured=false" in result.stdout


def test_heartbeat_example(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "examples" / "heartbeat.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=_env(tmp_path / "heartbeat"),
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "tick_count=" in result.stdout
    assert "reasoning_count=0" in result.stdout
    assert "llm_configured=false" in result.stdout
    tick_line = next(line for line in result.stdout.splitlines() if line.startswith("tick_count="))
    assert int(tick_line.split("=", 1)[1]) >= 3


def test_plugins_example(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "examples" / "plugins.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=_env(tmp_path / "plugins"),
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "plugin=sample" in result.stdout
    assert "status=loaded" in result.stdout
    assert "denied=true" in result.stdout
    assert "tick_count=0" in result.stdout


def test_agents_example(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "examples" / "agents.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=_env(tmp_path / "agents"),
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "tool=echo" in result.stdout
    assert "result=Draft the notes" in result.stdout
    assert "finish=Completed Draft the notes." in result.stdout
    assert "next=none" in result.stdout
    assert "tick_count=0" in result.stdout


def test_goals_example(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "examples" / "goals.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=_env(tmp_path / "goals"),
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "first=Draft the notes" in result.stdout
    assert "second=Revise the notes" in result.stdout
    assert "status=active" in result.stdout
    assert "tick_count=0" in result.stdout
    assert "llm_configured=false" in result.stdout


def test_brain_example(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "examples" / "brain.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=_env(tmp_path / "brain"),
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "provider=echo" in result.stdout
    assert "first=ping" in result.stdout
    assert "second=pong" in result.stdout
    assert "requests=2" in result.stdout
    assert "tick_count=0" in result.stdout


def test_memory_example(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "examples" / "memory.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=_env(tmp_path / "memory"),
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "survived=true" in result.stdout
    assert "tick_count=0" in result.stdout
    assert "llm_configured=false" in result.stdout


def test_events_example(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "examples" / "events.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=_env(tmp_path / "events"),
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "delivered=1" in result.stdout
    assert "tick_count=0" in result.stdout
    assert "llm_configured=false" in result.stdout


def test_recovery_example(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "examples" / "recovery_after_restart.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=_env(tmp_path / "recovery"),
    )
    assert result.returncode == 0, result.stderr
    assert "recovered recovery_count=1" in result.stdout
