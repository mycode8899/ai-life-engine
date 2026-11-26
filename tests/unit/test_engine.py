# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from ai_life_engine import (
    CheckResult,
    CheckStatus,
    EngineConfig,
    HealthStatus,
    LifecycleError,
    LifecycleState,
    LifeEngine,
)


def test_start_pause_resume_and_idempotent_stop(
    engine: LifeEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ai_life_engine")
    first = engine.start()
    assert first.state is LifecycleState.RUNNING
    assert first.persisted_state is LifecycleState.RUNNING
    assert first.owns_runtime is True
    assert first.clean_shutdown is False
    assert first.pid == __import__("os").getpid()
    assert engine.health().status is HealthStatus.HEALTHY
    assert "to=running" in caplog.text

    paused = engine.pause()
    assert paused.state is LifecycleState.PAUSED
    assert paused.pause_count == 1
    assert paused.run_id == first.run_id
    assert engine.health().status is HealthStatus.HEALTHY

    resumed = engine.resume()
    assert resumed.state is LifecycleState.RUNNING
    assert resumed.run_id == first.run_id
    assert resumed.started_at == first.started_at

    stopped = engine.stop(reason="done")
    assert stopped.state is LifecycleState.STOPPED
    assert stopped.clean_shutdown is True
    assert stopped.run_id == first.run_id
    again = engine.stop()
    assert again.state is LifecycleState.STOPPED
    assert engine.owns_runtime is False

    with pytest.raises(LifecycleError) as paused_twice:
        engine.pause()
    assert paused_twice.value.code == "not_running"


def test_double_start_and_close_while_running_are_rejected(engine: LifeEngine) -> None:
    engine.start()
    with pytest.raises(LifecycleError) as caught:
        engine.start()
    assert caught.value.code == "already_running"
    with pytest.raises(LifecycleError) as closed:
        engine.close()
    assert closed.value.code == "still_active"
    engine.stop()
    engine.close()
    with pytest.raises(LifecycleError) as used:
        engine.status()
    assert used.value.code == "closed"


def test_context_manager_shuts_down_on_success_and_failure(config: EngineConfig) -> None:
    with LifeEngine(config) as engine:
        engine_id = engine.engine_id
        assert engine.state is LifecycleState.RUNNING
    follower = LifeEngine(config)
    try:
        snapshot = follower.status()
        assert snapshot.engine_id == engine_id
        assert snapshot.persisted_state is LifecycleState.STOPPED
        assert snapshot.clean_shutdown is True
        assert snapshot.recovery_required is False
    finally:
        follower.close()

    with pytest.raises(RuntimeError, match="boom"):
        with LifeEngine(config) as engine:
            raise RuntimeError("boom")
    follower = LifeEngine(config)
    try:
        assert follower.status().clean_shutdown is True
    finally:
        follower.close()


def test_second_handle_cannot_claim_a_live_runtime(config: EngineConfig) -> None:
    owner = LifeEngine(config)
    owner.start()
    other = LifeEngine(config)
    try:
        assert other.status().blocked_by_live_process is True
        assert other.health().status is HealthStatus.DEGRADED
        with pytest.raises(LifecycleError) as caught:
            other.start()
        assert caught.value.code == "foreign_runtime"
        assert owner.health().status is HealthStatus.HEALTHY
    finally:
        other.close()
        owner.stop()
        owner.close()


def test_identity_metadata_and_recovery_count_survive_restart(config: EngineConfig) -> None:
    with LifeEngine(config) as engine:
        engine_id = engine.engine_id
        first_run = engine.status().run_id
    renamed = EngineConfig(data_dir=config.data_dir, name="renamed", metadata={"role": "demo"})
    engine = LifeEngine(renamed)
    try:
        started = engine.start()
        assert started.engine_id == engine_id
        assert started.name == "renamed"
        assert started.metadata == {"role": "demo"}
        assert started.run_id != first_run
        assert started.recovery_count == 0
        preserved = LifeEngine(EngineConfig(data_dir=config.data_dir))
        try:
            assert preserved.status().name == "renamed"
        finally:
            preserved.close()
    finally:
        engine.stop()
        engine.close()

    cleared = LifeEngine(EngineConfig(data_dir=config.data_dir, metadata={}))
    try:
        cleared.start()
        assert cleared.status().metadata == {}
        assert cleared.status().name == "renamed"
    finally:
        cleared.stop()
        cleared.close()


def test_initialize_is_idempotent(config: EngineConfig) -> None:
    engine = LifeEngine(config)
    try:
        created = engine.initialize()
        assert created.persisted_state is LifecycleState.STOPPED
        assert created.clean_shutdown is True
        again = engine.initialize()
        assert again.engine_id == created.engine_id
        assert again.updated_at == created.updated_at
    finally:
        engine.close()


def test_run_waits_until_stop_is_requested(engine: LifeEngine) -> None:
    async def scenario() -> None:
        async def stopper() -> None:
            for _ in range(200):
                if engine.state is LifecycleState.RUNNING:
                    engine.request_stop("test")
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("runtime did not start")

        await asyncio.wait_for(asyncio.gather(engine.run(), stopper()), timeout=5)

    asyncio.run(scenario())
    assert engine.state is LifecycleState.STOPPED
    assert engine.status().clean_shutdown is True
    actions = [entry.action for entry in engine.audit_trail()]
    assert "lifecycle.running" in actions
    assert "lifecycle.stopped" in actions
    assert engine.checkpoints(limit=1)[0].state is LifecycleState.STOPPED


def test_audit_limit_rejects_booleans(engine: LifeEngine) -> None:
    with pytest.raises(LifecycleError) as caught:
        engine.audit_trail(limit=True)  # type: ignore[arg-type]
    assert caught.value.code == "invalid_argument"


def test_custom_health_checks(engine: LifeEngine) -> None:
    class Warn:
        name = "budget"

        def run(self, snapshot: object) -> CheckResult:
            del snapshot
            return CheckResult(name="budget", status=CheckStatus.WARN, message="budget is low")

    engine.register_health_check(Warn())
    engine.start()
    assert engine.health().status is HealthStatus.DEGRADED

    class Boom:
        name = "boom"

        def run(self, snapshot: object) -> CheckResult:
            del snapshot
            raise RuntimeError("nope")

    engine.register_health_check(Boom())
    report = engine.health()
    assert report.status is HealthStatus.UNHEALTHY
    assert any(check.name == "boom" and check.status is CheckStatus.FAIL for check in report.checks)
    assert engine.state is LifecycleState.RUNNING


def test_health_check_cannot_stop_the_runtime(engine: LifeEngine) -> None:
    class Sneaky:
        name = "sneaky"

        def run(self, snapshot: object) -> CheckResult:
            del snapshot
            engine.stop()
            return CheckResult(name="sneaky", status=CheckStatus.PASS, message="stopped")

    engine.register_health_check(Sneaky())
    engine.start()
    report = engine.health()
    assert report.status is HealthStatus.UNHEALTHY
    assert engine.state is LifecycleState.RUNNING


def test_reserved_and_duplicate_checks_are_rejected(engine: LifeEngine) -> None:
    class Storage:
        name = "storage"

        def run(self, snapshot: object) -> CheckResult:
            del snapshot
            return CheckResult(name="storage", status=CheckStatus.PASS, message="no")

    with pytest.raises(Exception) as caught:
        engine.register_health_check(Storage())
    assert "reserved" in str(caught.value)

    class Budget:
        name = "budget"

        def run(self, snapshot: object) -> CheckResult:
            del snapshot
            return CheckResult(name="budget", status=CheckStatus.PASS, message="ok")

    engine.register_health_check(Budget())
    with pytest.raises(Exception, match="already registered"):
        engine.register_health_check(Budget())


def test_storage_outage_is_unhealthy(engine: LifeEngine) -> None:
    engine.start()
    engine._store.close()  # type: ignore[attr-defined]
    report = engine.health()
    assert report.status is HealthStatus.UNHEALTHY
    failed = [check for check in report.checks if check.name == "storage"]
    assert failed and failed[0].status is CheckStatus.FAIL
    engine._owns_runtime = False
    engine._closed = True


def test_file_data_dir_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "not-a-directory"
    target.write_text("file", encoding="utf-8")
    with pytest.raises(Exception, match="file"):
        LifeEngine(EngineConfig(data_dir=target))
