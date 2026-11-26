# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from ai_life_engine import EngineConfig, HealthStatus, LifecycleState, LifeEngine
from ai_life_engine.persistence import EngineRecord, NewAudit, NewCheckpoint, SqliteStateStore


def test_dead_process_is_recovered(config: EngineConfig, dead_pid: int) -> None:
    now = datetime.now(UTC)
    engine_id = uuid4()
    record = EngineRecord(
        engine_id=engine_id,
        name="crash",
        created_at=now,
        updated_at=now,
        state=LifecycleState.RUNNING,
        run_id=uuid4(),
        pid=dead_pid,
        started_at=now,
        clean_shutdown=False,
        software_version="0.1.0",
        metadata={"note": "keep"},
    )
    store = SqliteStateStore(config.database_path())
    store.commit_state(
        record,
        expected_generation=None,
        audit=NewAudit(
            engine_id=engine_id,
            run_id=record.run_id,
            action="lifecycle.running",
            from_state=LifecycleState.STARTING,
            to_state=LifecycleState.RUNNING,
            detail="start",
            created_at=now,
        ),
        checkpoint=NewCheckpoint(
            engine_id=engine_id,
            run_id=record.run_id,
            state=LifecycleState.RUNNING,
            reason="start",
            created_at=now,
            detail={"previous_state": "starting"},
        ),
    )
    store.close()

    engine = LifeEngine(EngineConfig(data_dir=config.data_dir))
    try:
        before = engine.status()
        assert before.engine_id == engine_id
        assert before.recovery_required is True
        assert before.metadata == {"note": "keep"}
        assert engine.health().status is HealthStatus.DEGRADED
        started = engine.start()
        assert started.state is LifecycleState.RUNNING
        assert started.recovery_count == 1
        assert started.metadata == {"note": "keep"}
        assert started.run_id != record.run_id
        engine.stop()
        assert engine.status().recovery_count == 1
        assert engine.status().clean_shutdown is True
    finally:
        if engine.owns_runtime:
            engine.stop()
        engine.close()

    restarted = LifeEngine(config)
    try:
        assert restarted.status().recovery_required is False
        restarted.start()
        assert restarted.status().recovery_count == 1
    finally:
        if restarted.owns_runtime:
            restarted.stop()
        restarted.close()
