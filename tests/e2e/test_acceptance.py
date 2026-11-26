# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

from ai_life_engine import EngineConfig, HealthStatus, LifecycleState, LifeEngine


def test_acceptance_initialize_and_run_without_an_llm(tmp_path: Path) -> None:
    config = EngineConfig(data_dir=tmp_path / "acceptance", name="acceptance")
    with LifeEngine(config) as engine:
        assert engine.capabilities["llm"] is True
        assert engine.brain.configured is False
        assert engine.capabilities["heartbeat"] is True
        assert engine.capabilities["event_bus"] is True
        assert engine.capabilities["memory"] is True
        assert engine.capabilities["lifecycle"] is True
        assert engine.health().status is HealthStatus.HEALTHY
        assert engine.status().state is LifecycleState.RUNNING
    follow = LifeEngine(config)
    try:
        snapshot = follow.status()
        assert snapshot.persisted_state is LifecycleState.STOPPED
        assert snapshot.clean_shutdown is True
        assert snapshot.recovery_required is False
    finally:
        follow.close()


def test_heartbeat_ticks_without_calling_a_model(tmp_path: Path) -> None:
    calls = {"count": 0}

    class Probe:
        def reason(self, observation: object) -> str:
            del observation
            calls["count"] += 1
            return "no"

    config = EngineConfig(
        data_dir=tmp_path / "ticks",
        name="ticks",
        heartbeat_interval=0.05,
    )
    engine = LifeEngine(config, reasoner=Probe())

    async def _stop_after() -> None:
        for _ in range(500):
            status = engine.heartbeat.status()
            if status.tick_count >= 3:
                assert engine.health().status is HealthStatus.HEALTHY
                engine.request_stop("enough")
                return
            await asyncio.sleep(0.01)
        engine.request_stop("timed out")

    async def _main() -> None:
        await asyncio.gather(engine.run(), _stop_after())

    try:
        asyncio.run(_main())
        status = engine.heartbeat.status()
        assert status.tick_count >= 3
        assert status.reasoning_count == 0
        assert calls["count"] == 0
        assert engine.capabilities["llm"] is True
        assert engine.brain.configured is False
    finally:
        engine.close()


def test_import_does_not_touch_the_working_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    import ai_life_engine

    importlib.reload(ai_life_engine)
    assert list(tmp_path.iterdir()) == []


def test_unimplemented_layers_are_not_importable() -> None:
    for name in ("adapters",):
        try:
            importlib.import_module(f"ai_life_engine.{name}")
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"{name} should not exist until its phase is implemented")


def test_runtime_sources_do_not_import_optional_providers() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "ai_life_engine"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    for name in ("openai", "anthropic", "ollama", "fastapi", "langgraph", "crewai", "autogen"):
        assert f"import {name}" not in text
        assert f"from {name}" not in text
