# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_life_engine import (
    AgentError,
    EchoAgent,
    EngineConfig,
    LifecycleError,
    LifeEngine,
    ScriptedAgent,
)
from ai_life_engine.agents.models import ToolInfo
from ai_life_engine.core.health import CheckStatus, HealthStatus
from ai_life_engine.goals.models import GoalStatus
from ai_life_engine.heartbeat.models import WakeReason
from tests.support.scripted import ScriptedScheduler


class _Shout:
    name = "shout"
    description = "Uppercase the argument."
    permission = "shout"

    def call(self, argument: str) -> str:
        return argument.upper()


def test_tools_are_denied_until_granted(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "denied", name="denied"))
    try:
        assert engine.capabilities["tool_registry"] is True
        assert engine.capabilities["agent_adapters"] is True
        names = [tool.name for tool in engine.agents.tools()]
        assert names == ["echo", "finish", "note"]
        with pytest.raises(LifecycleError):
            engine.agents.call("echo", "hello")
        engine.initialize()
        with pytest.raises(AgentError) as caught:
            engine.agents.call("echo", "hello")
        assert caught.value.code == "denied"
        assert engine.memory.recall_working("task") is None
        engine.agents.allow("echo")
        assert engine.agents.call("echo", "hello") == "hello"
        engine.start()
        engine.agents.revoke("echo")
        with pytest.raises(AgentError):
            engine.agents.call("echo", "again")
        report = engine.health()
        assert report.status is HealthStatus.DEGRADED
        check = next(item for item in report.checks if item.name == "agents")
        assert check.status is CheckStatus.WARN
        engine.stop()
    finally:
        if engine.owns_runtime:
            engine.stop()
        engine.close()

    follow = LifeEngine(EngineConfig(data_dir=tmp_path / "denied"))
    try:
        assert follow.agents.grants() == []
        assert follow.agents.calls(limit=5)[0].status.value == "denied"
    finally:
        follow.close()


def test_adapter_switch_note_echo_and_finish(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "switch", name="switch"))
    try:
        engine.initialize()
        engine.agents.allow("memory.write")
        engine.agents.use(ScriptedAgent([("note", "Saved from the agent.")]))
        noted = engine.agents.step()
        assert noted.executed is True
        assert noted.result == "Noted."
        assert engine.memory.search("Saved from the agent.")
        engine.agents.allow("echo")
        engine.agents.use(EchoAgent())
        waiting = engine.agents.step()
        assert waiting.executed is False
        goal = engine.goals.add("Write notes", "Keep a durable next step.", priority=1)
        engine.goals.plan(goal.goal_id, ["Draft the notes"])
        echoed = engine.agents.step()
        assert echoed.tool == "echo"
        assert echoed.result == "Draft the notes"
        engine.agents.allow("goals.write")
        done = engine.agents.call("finish", "Drafted the notes.")
        assert done == "Completed Draft the notes."
        assert engine.goals.get(goal.goal_id).status is GoalStatus.DONE
        assert engine.goals.next_action() is None
        engine.agents.register(_Shout())
        engine.agents.allow("shout")
        assert engine.agents.call("shout", "quiet") == "QUIET"
    finally:
        engine.close()


def test_the_heartbeat_does_not_call_a_granted_tool(tmp_path: Path) -> None:
    config = EngineConfig(data_dir=tmp_path / "ticks", heartbeat_interval=1)
    scheduler = ScriptedScheduler([WakeReason.INTERVAL, WakeReason.STOP])
    engine = LifeEngine(config, scheduler=scheduler)
    engine.agents.allow("echo")
    engine.agents.use(ScriptedAgent([("echo", "do not run")]))
    try:
        engine.initialize()
        goal = engine.goals.add("Tick", "Be visible to the heartbeat.")
        engine.goals.plan(goal.goal_id, ["Look at the queue"])
        asyncio.run(engine.run())
        assert engine.heartbeat.status().tick_count == 1
        assert engine.agents.calls(limit=5) == []
        assert engine.goals.next_action() is not None
    finally:
        engine.close()


def test_tool_metadata_rejects_a_bad_registration(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "meta"))
    try:
        with pytest.raises(AgentError) as caught:
            engine.agents.register(_Shout())
            engine.agents.register(_Shout())
        assert caught.value.code == "conflict"
        info = ToolInfo(name="shout", description="Uppercase the argument.", permission="shout")
        assert info.permission == "shout"
    finally:
        engine.close()
