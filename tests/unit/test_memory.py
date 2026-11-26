# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from ai_life_engine import (
    EngineConfig,
    HealthStatus,
    LifecycleError,
    LifeEngine,
    MemoryKind,
)
from ai_life_engine.exceptions import ConfigurationError
from ai_life_engine.memory.models import EPISODE_RETENTION, FACT_LIMIT, WORKING_LIMIT


def test_memory_requires_an_identity_and_rejects_secrets(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "fresh"))
    try:
        with pytest.raises(LifecycleError):
            engine.memory.remember_working("task", "review")
        engine.initialize()
        with pytest.raises(LifecycleError):
            engine.memory.remember_working("task", "review", payload={"api_key": "nope"})
        with pytest.raises(LifecycleError):
            engine.memory.remember_fact("Bad Key", "nope")
        with pytest.raises(LifecycleError):
            engine.memory.search("   ")
    finally:
        engine.close()


def test_memory_survives_a_restart_and_can_be_searched(tmp_path: Path) -> None:
    config = EngineConfig(data_dir=tmp_path / "memory", name="memory")
    writer = LifeEngine(config)
    try:
        writer.initialize()
        writer.memory.remember_working("task", "Review the log")
        episode = writer.memory.remember_episode(
            "The runtime started without a model.",
            source="application",
            payload={"from": "test"},
        )
        fact = writer.memory.remember_fact("project", "AI Life Engine stores memory in SQLite.")
        writer.memory.remember_fact("project", "Memory is stored in SQLite.")
        replaced = writer.memory.recall_fact("project")
        assert replaced is not None
        assert replaced.fact_id == fact.fact_id
        assert replaced.content == "Memory is stored in SQLite."
        assert writer.memory.counts().facts == 1
        assert episode.summary.startswith("The runtime")
        assert writer.heartbeat.status().tick_count == 0
        writer.start()
        assert writer.health().status is HealthStatus.HEALTHY
        writer.stop()
    finally:
        writer.close()

    reader = LifeEngine(config)
    try:
        assert reader.memory.recall_working("task") is not None
        assert reader.memory.recall_working("task").value == "Review the log"
        assert reader.memory.episodes(limit=5)[0].summary.startswith("The runtime")
        hits = reader.memory.search("sqlite")
        assert [hit.kind for hit in hits] == [MemoryKind.FACT]
        assert reader.memory.search("MODEL", kind="episode")[0].text.startswith("The runtime")
        assert reader.memory.search("review", kind=MemoryKind.WORKING)[0].key == "task"
        assert reader.capabilities["llm"] is True
        assert reader.brain.configured is False
        assert reader.capabilities["memory"] is True
    finally:
        reader.close()


def test_forget_and_capacity(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "cap", name="cap"))
    try:
        engine.initialize()
        for index in range(WORKING_LIMIT):
            engine.memory.remember_working(f"k{index}", f"value {index}")
        with pytest.raises(LifecycleError):
            engine.memory.remember_working("overflow", "nope")
        engine.memory.remember_working("k0", "replaced")
        assert engine.memory.recall_working("k0").value == "replaced"
        engine.memory.forget_working("k0")
        assert engine.memory.recall_working("k0") is None
        with pytest.raises(LifecycleError):
            engine.memory.forget_working("k0")
        engine.memory.clear_working()
        assert engine.memory.working() == []

        for index in range(FACT_LIMIT):
            engine.memory.remember_fact(f"f{index}", f"fact {index}")
        with pytest.raises(LifecycleError):
            engine.memory.remember_fact("overflow", "nope")

        for index in range(EPISODE_RETENTION + 1):
            engine.memory.remember_episode(f"episode {index}")
        stored = engine.memory.episodes(limit=EPISODE_RETENTION + 1)
        assert len(stored) == EPISODE_RETENTION
        texts = {item.summary for item in stored}
        assert "episode 0" not in texts
        assert f"episode {EPISODE_RETENTION}" in texts
    finally:
        engine.close()


def test_store_without_memory_methods_can_boot(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "plain", name="plain"))
    try:
        engine.initialize()
        engine._store.upsert_working = None  # noqa: SLF001
        with pytest.raises(ConfigurationError):
            engine.memory.remember_working("task", "review")
        assert engine.health().status is HealthStatus.INACTIVE
    finally:
        engine.close()
