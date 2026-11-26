# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from ai_life_engine import EngineConfig, HealthStatus, LifecycleState, LifeEngine
from tests.support.memory_store import MemoryStateStore


def test_engine_runs_on_a_custom_store(tmp_path) -> None:
    store = MemoryStateStore()
    config = EngineConfig(data_dir=tmp_path / "unused", name="memory")
    engine = LifeEngine(config, store=store)
    try:
        assert engine.capabilities["llm"] is True
        assert engine.brain.configured is False
        engine.start()
        assert engine.state is LifecycleState.RUNNING
        assert engine.health().status is HealthStatus.HEALTHY
        engine.pause()
        engine.resume()
        engine.stop()
        assert engine.status().clean_shutdown is True
    finally:
        engine.close()

    again = LifeEngine(config, store=store)
    try:
        assert again.engine_id == engine.engine_id
        assert again.status().persisted_state is LifecycleState.STOPPED
        again.start()
        assert again.status().recovery_count == 0
    finally:
        if again.owns_runtime:
            again.stop()
        again.close()
        store.close()
