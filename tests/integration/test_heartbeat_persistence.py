# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ai_life_engine import EngineConfig, HeartbeatStatus, LifeEngine, WakeReason
from ai_life_engine.heartbeat.models import (
    HEARTBEAT_TICK_RETENTION,
    HeartbeatSnapshot,
    NewHeartbeatTick,
)
from ai_life_engine.persistence import SCHEMA_VERSION, SqliteStateStore
from tests.support.scripted import ScriptedScheduler


def test_ticks_survive_a_new_process(tmp_path: Path) -> None:
    config = EngineConfig(data_dir=tmp_path / "persist", name="persist", heartbeat_interval=1)
    first = LifeEngine(
        config,
        scheduler=ScriptedScheduler([WakeReason.INTERVAL, WakeReason.INTERVAL, WakeReason.STOP]),
    )
    try:
        asyncio.run(first.run())
        assert first.heartbeat.status().tick_count == 2
    finally:
        first.close()

    second = LifeEngine(
        config,
        scheduler=ScriptedScheduler([WakeReason.INTERVAL, WakeReason.STOP]),
    )
    try:
        asyncio.run(second.run())
        status = second.heartbeat.status()
        assert status.tick_count == 3
        assert status.recovered is False
        assert status.reasoning_count == 0
        assert len(second.heartbeat.ticks(limit=10)) == 3
    finally:
        second.close()


def test_sleep_survives_restart_until_wake(tmp_path: Path) -> None:
    config = EngineConfig(data_dir=tmp_path / "asleep", name="asleep", heartbeat_interval=1)
    first = LifeEngine(config, scheduler=ScriptedScheduler([WakeReason.STOP]))
    try:
        first.start()
        first.heartbeat.sleep()
        assert first.heartbeat.status().status is HeartbeatStatus.ASLEEP
    finally:
        if first.owns_runtime:
            first.stop()
        first.close()

    second = LifeEngine(
        config,
        scheduler=ScriptedScheduler([WakeReason.INTERVAL, WakeReason.STOP]),
    )
    try:
        asyncio.run(second.run())
        assert second.heartbeat.status().tick_count == 0
        assert second.heartbeat.status().status is HeartbeatStatus.ASLEEP
        second.heartbeat.wake("resume")
        asyncio.run(second.run())
        assert second.heartbeat.status().tick_count == 1
        assert second.heartbeat.status().asleep is False
    finally:
        second.close()


def test_in_progress_heartbeat_is_recovered(tmp_path: Path) -> None:
    config = EngineConfig(data_dir=tmp_path / "crash", name="crash", heartbeat_interval=1)
    engine = LifeEngine(config)
    try:
        engine.start()
        now = datetime.now(UTC)
        engine._store.commit_heartbeat(  # noqa: SLF001
            HeartbeatSnapshot(
                engine_id=engine.engine_id,
                status=HeartbeatStatus.BEATING,
                interval_seconds=1,
                tick_count=4,
                in_progress=True,
                updated_at=now,
            ),
            expected_generation=None,
        )
    finally:
        engine.stop()
        engine.close()

    follow = LifeEngine(config, scheduler=ScriptedScheduler([WakeReason.STOP]))
    try:
        asyncio.run(follow.run())
        status = follow.heartbeat.status()
        assert status.recovered is True
        assert status.tick_count == 4
        assert status.in_progress is False
        assert status.last_error == "Recovered an in-progress heartbeat."
    finally:
        follow.close()


def test_schema_v1_upgrades_and_ticks_are_capped(tmp_path: Path) -> None:
    path = tmp_path / "engine.sqlite3"
    store = SqliteStateStore(path)
    store.close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_meta SET value = '1' WHERE key = 'schema_version'")
    conn.execute("DROP TABLE heartbeat_ticks")
    conn.execute("DROP TABLE heartbeat_state")
    conn.execute("DROP TABLE IF EXISTS working_memory")
    conn.execute("DROP TABLE IF EXISTS episodes")
    conn.execute("DROP TABLE IF EXISTS facts")
    conn.execute("DROP TABLE IF EXISTS model_usage")
    conn.execute("DROP TABLE IF EXISTS reflections")
    conn.execute("DROP TABLE IF EXISTS plan_steps")
    conn.execute("DROP TABLE IF EXISTS plans")
    conn.execute("DROP TABLE IF EXISTS goals")
    conn.execute("DROP TABLE IF EXISTS tool_calls")
    conn.commit()
    conn.close()

    upgraded = SqliteStateStore(path)
    upgraded.close()
    conn = sqlite3.connect(path)
    version = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert version is not None
    assert version[0] == SCHEMA_VERSION == "7"
    assert "heartbeat_state" in tables
    assert "heartbeat_ticks" in tables
    assert "events" in tables
    assert "working_memory" in tables
    assert "episodes" in tables
    assert "facts" in tables
    assert "model_usage" in tables
    assert "goals" in tables
    assert "plans" in tables
    assert "plan_steps" in tables
    assert "reflections" in tables
    assert "tool_calls" in tables

    config = EngineConfig(data_dir=tmp_path / "cap", heartbeat_interval=1)
    engine = LifeEngine(config)
    try:
        engine.start()
        now = datetime.now(UTC)
        current = None
        for index in range(HEARTBEAT_TICK_RETENTION + 1):
            current = engine._store.commit_heartbeat(  # noqa: SLF001
                HeartbeatSnapshot(
                    engine_id=engine.engine_id,
                    status=HeartbeatStatus.BEATING,
                    interval_seconds=1,
                    tick_count=index + 1,
                    updated_at=now,
                ),
                expected_generation=None if current is None else current.generation,
                tick=_tick(engine.engine_id, index + 1, now),
            )
        assert current is not None
        listed = engine.heartbeat.ticks(limit=100)
        assert len(listed) == HEARTBEAT_TICK_RETENTION
        assert listed[0].tick_number == HEARTBEAT_TICK_RETENTION + 1
    finally:
        engine.stop()
        engine.close()


def _tick(engine_id, number: int, now: datetime) -> NewHeartbeatTick:
    return NewHeartbeatTick(
        engine_id=engine_id,
        tick_number=number,
        wake_reason="interval",
        reasoning_required=False,
        reasoning_executed=False,
        task_count=0,
        runtime_state="running",
        detail=f"tick {number}",
        created_at=now,
    )
