# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from ai_life_engine.cli import main


def _env() -> dict[str, str]:
    env = os.environ.copy()
    src = Path(__file__).resolve().parents[2] / "src"
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(src) if not previous else str(src) + os.pathsep + previous
    env["PYTHONUNBUFFERED"] = "1"
    return env


def test_version_init_status_and_health(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.8.0"

    data = tmp_path / "cli"
    assert main(["init", "--data-dir", str(data), "--name", "demo", "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["name"] == "demo"
    assert created["persisted_state"] == "stopped"

    assert main(["status", "--data-dir", str(data), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["engine_id"] == created["engine_id"]
    assert status["recovery_required"] is False

    assert main(["health", "--data-dir", str(data), "--expect", "running"]) == 1
    capsys.readouterr()
    assert main(["health", "--data-dir", str(data), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "inactive"
    names = [check["name"] for check in report["checks"]]
    assert "heartbeat" in names

    assert main(["heartbeat", "--data-dir", str(data), "--json"]) == 0
    heartbeat = json.loads(capsys.readouterr().out)
    assert heartbeat["status"] == "idle"
    assert heartbeat["reasoning_count"] == 0
    assert heartbeat["tick_count"] == 0

    assert main(["events", "--data-dir", str(data), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert (
        main(
            [
                "publish",
                "--data-dir",
                str(data),
                "--type",
                "ping",
                "--payload",
                "from=cli",
            ]
        )
        == 0
    )
    published = capsys.readouterr().out
    assert "type=ping" in published
    assert main(["events", "--data-dir", str(data), "--json"]) == 0
    stored = json.loads(capsys.readouterr().out)
    assert stored[0]["type"] == "ping"
    assert stored[0]["status"] == "pending"
    assert stored[0]["payload"] == {"from": "cli"}

    assert main(["memory", "--data-dir", str(data), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"episodes": 0, "facts": 0, "working": 0}
    assert (
        main(
            [
                "remember",
                "--data-dir",
                str(data),
                "--kind",
                "fact",
                "--key",
                "project",
                "--content",
                "Memory is stored in SQLite.",
            ]
        )
        == 0
    )
    assert "kind=fact" in capsys.readouterr().out
    assert main(["recall", "--data-dir", str(data), "--query", "sqlite", "--json"]) == 0
    hits = json.loads(capsys.readouterr().out)
    assert hits[0]["kind"] == "fact"
    assert hits[0]["key"] == "project"

    assert main(["brain", "--data-dir", str(data), "--json"]) == 0
    brain = json.loads(capsys.readouterr().out)
    assert brain["configured"] is False
    assert brain["requests"] == 0
    assert (
        main(
            [
                "complete",
                "--data-dir",
                str(data),
                "--provider",
                "echo",
                "--prompt",
                "hello",
            ]
        )
        == 0
    )
    completed = capsys.readouterr().out
    assert "provider=echo" in completed
    assert "text=hello" in completed
    assert main(["brain", "--data-dir", str(data), "--json"]) == 0
    after = json.loads(capsys.readouterr().out)
    assert after["requests"] == 1
    assert after["configured"] is False

    assert (
        main(
            [
                "goal-add",
                "--data-dir",
                str(data),
                "--title",
                "Write notes",
                "--objective",
                "Keep a durable next step.",
                "--priority",
                "1",
            ]
        )
        == 0
    )
    added = capsys.readouterr().out
    goal_id = ""
    for line in added.splitlines():
        if line.startswith("goal_id="):
            goal_id = line.split("=", 1)[1]
    assert (
        main(
            [
                "goal-plan",
                "--data-dir",
                str(data),
                "--goal-id",
                goal_id,
                "--step",
                "Draft",
                "--step",
                "Revise",
            ]
        )
        == 0
    )
    assert main(["goal-next", "--data-dir", str(data)]) == 0
    assert "action=Draft" in capsys.readouterr().out
    assert (
        main(
            [
                "goal-reflect",
                "--data-dir",
                str(data),
                "--goal-id",
                goal_id,
                "--outcome",
                "succeeded",
                "--note",
                "Drafted the notes.",
            ]
        )
        == 0
    )
    assert "goal_status=active" in capsys.readouterr().out
    assert main(["goal-next", "--data-dir", str(data)]) == 0
    assert "action=Revise" in capsys.readouterr().out

    assert main(["agents", "--data-dir", str(data), "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["adapter"] == ""
    assert listed["grants"] == []
    assert {tool["name"] for tool in listed["tools"]} == {"echo", "finish", "note"}
    denied = main(["tool-call", "--data-dir", str(data), "--tool", "echo", "--argument", "hello"])
    assert denied == 2
    assert (
        main(
            [
                "tool-call",
                "--data-dir",
                str(data),
                "--tool",
                "echo",
                "--argument",
                "hello",
                "--grant",
                "echo",
            ]
        )
        == 0
    )
    assert "result=hello" in capsys.readouterr().out
    assert main(["plugins", "--data-dir", str(data)]) == 0
    assert capsys.readouterr().out.strip() == "plugins=none"


def test_run_stops_on_sigterm(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    if os.name != "posix":
        pytest.skip("SIGTERM shutdown is verified on POSIX.")
    data = tmp_path / "run"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ai_life_engine",
            "run",
            "--data-dir",
            str(data),
            "--name",
            "signal",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(),
    )
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert line.startswith("running "), line
    proc.send_signal(signal.SIGTERM)
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 0, stderr
    assert "clean_shutdown=true" in stdout
    assert main(["status", "--data-dir", str(data), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["persisted_state"] == "stopped"
    assert status["clean_shutdown"] is True
    assert status["recovery_required"] is False


def test_serve_stops_on_sigterm(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("SIGTERM shutdown is verified on POSIX.")
    data = tmp_path / "serve"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ai_life_engine",
            "serve",
            "--data-dir",
            str(data),
            "--name",
            "serve",
            "--port",
            "0",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(),
    )
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert line.startswith("listening=127.0.0.1:"), line
    proc.send_signal(signal.SIGTERM)
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 0, stderr
    assert "clean_shutdown=true" in stdout
