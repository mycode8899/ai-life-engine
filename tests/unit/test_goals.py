# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_life_engine import (
    EngineConfig,
    GoalError,
    LifecycleError,
    LifeEngine,
    ModelError,
)
from ai_life_engine.brain.providers import ScriptedProvider
from ai_life_engine.core.health import CheckStatus, HealthStatus
from ai_life_engine.goals.models import GOAL_LIMIT, GoalStatus
from ai_life_engine.heartbeat.models import WakeReason
from tests.support.scripted import ScriptedScheduler


def test_goals_require_an_identity(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "fresh"))
    try:
        assert engine.capabilities["goals"] is True
        assert engine.capabilities["planning"] is True
        assert engine.capabilities["reflection"] is True
        with pytest.raises(LifecycleError):
            engine.goals.add("Notes", "Keep a next step.")
        engine.initialize()
        goal = engine.goals.add("Notes", "Keep a next step.", priority=2)
        assert goal.status is GoalStatus.ACTIVE
        assert engine.goals.next_action() is None
    finally:
        engine.close()


def test_plan_next_action_and_reflection_survive_restart(tmp_path: Path) -> None:
    data = tmp_path / "plans"
    engine = LifeEngine(EngineConfig(data_dir=data, name="plans"))
    try:
        engine.initialize()
        later = engine.goals.add("Later", "Do this second.", priority=3)
        first = engine.goals.add("First", "Do this first.", priority=1)
        engine.goals.set_status(first.goal_id, GoalStatus.BLOCKED)
        engine.goals.plan(first.goal_id, ["Blocked step"])
        engine.goals.plan(later.goal_id, ["Draft", "Revise"])
        current = engine.goals.next_action()
        assert current is not None
        assert current.goal_id == later.goal_id
        assert current.action == "Draft"
        engine.goals.reflect(later.goal_id, outcome="succeeded", note="Drafted it.")
        advanced = engine.goals.next_action()
        assert advanced is not None
        assert advanced.action == "Revise"
        engine.goals.reflect(later.goal_id, outcome="skipped", note="Revision was unnecessary.")
        assert engine.goals.next_action() is None
        assert engine.goals.get(later.goal_id).status is GoalStatus.DONE
        assert engine.goals.reflections(later.goal_id)[0].outcome.value == "skipped"
    finally:
        engine.close()

    follow = LifeEngine(EngineConfig(data_dir=data))
    try:
        assert follow.goals.get(later.goal_id).status is GoalStatus.DONE
        assert follow.goals.next_action() is None
        assert follow.goals.plans(later.goal_id)[0].status.value == "accepted"
    finally:
        follow.close()


def test_propose_uses_the_selected_model_and_health_warns(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "propose", name="propose"))
    try:
        engine.initialize()
        goal = engine.goals.add("Ship", "Publish the notes.")
        with pytest.raises(ModelError) as caught:
            engine.goals.propose(goal.goal_id)
        assert caught.value.code == "not_configured"
        engine.start()
        report = engine.health()
        assert report.status is HealthStatus.DEGRADED
        check = next(item for item in report.checks if item.name == "goals")
        assert check.status is CheckStatus.WARN
        engine.brain.use(ScriptedProvider(["Collect sources\n- Write the page"]))
        plan = engine.goals.propose(goal.goal_id)
        actions = [step.action for step in engine.goals.steps(plan.plan_id)]
        assert actions == ["Collect sources", "Write the page"]
        assert engine.brain.totals().requests == 1
        assert engine.health().status is HealthStatus.HEALTHY
        engine.stop()
    finally:
        if engine.owns_runtime:
            engine.stop()
        engine.close()


def test_a_goal_does_not_call_a_model_from_the_heartbeat(tmp_path: Path) -> None:
    config = EngineConfig(data_dir=tmp_path / "ticks", heartbeat_interval=1)
    scheduler = ScriptedScheduler([WakeReason.INTERVAL, WakeReason.STOP])
    engine = LifeEngine(config, scheduler=scheduler)
    engine.brain.use(ScriptedProvider(["Do not call this"]))
    try:
        engine.initialize()
        goal = engine.goals.add("Tick", "Be visible to the heartbeat.")
        engine.goals.plan(goal.goal_id, ["Look at the queue"])
        asyncio.run(engine.run())
        ticks = engine.heartbeat.ticks(limit=5)
        assert ticks[0].task_count == 1
        assert engine.heartbeat.status().reasoning_count == 0
        assert engine.brain.totals().requests == 0
        assert engine.goals.next_action() is not None
    finally:
        engine.close()


def test_goal_limit(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "full"))
    try:
        engine.initialize()
        for index in range(GOAL_LIMIT):
            engine.goals.add(f"Goal {index}", "Fill the store.")
        with pytest.raises(GoalError) as caught:
            engine.goals.add("One more", "This does not fit.")
        assert caught.value.code == "full"
    finally:
        engine.close()
