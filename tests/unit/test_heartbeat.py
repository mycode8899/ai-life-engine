# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_life_engine import (
    EngineConfig,
    HealthStatus,
    HeartbeatStatus,
    LifecycleState,
    LifeEngine,
    ReasoningDecision,
    WakeReason,
)
from ai_life_engine.core.time import utcnow
from ai_life_engine.heartbeat.health import heartbeat_check
from ai_life_engine.heartbeat.models import HeartbeatSnapshot
from ai_life_engine.heartbeat.scheduler import IntervalScheduler
from tests.support.memory_store import MemoryStateStore
from tests.support.scripted import IntervalUntilStop, ScriptedScheduler


def test_interval_bounds_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        EngineConfig(data_dir=tmp_path, heartbeat_interval=0)
    with pytest.raises(ValidationError):
        EngineConfig(data_dir=tmp_path, heartbeat_interval=True)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        EngineConfig(data_dir=tmp_path, heartbeat_max_failures=0)
    config = EngineConfig(data_dir=tmp_path, heartbeat_interval=0.05, heartbeat_max_failures=2)
    assert config.heartbeat_interval == 0.05
    assert config.heartbeat_max_failures == 2


def test_interval_scheduler_waits_and_prefers_stop() -> None:
    async def _scenario() -> None:
        scheduler = IntervalScheduler()
        wake = asyncio.Event()
        stop = asyncio.Event()
        started = asyncio.get_running_loop().time()
        reason = await scheduler.wait_for_wake(interval_seconds=0.05, wake=wake, stop=stop)
        assert reason is WakeReason.INTERVAL
        assert asyncio.get_running_loop().time() - started >= 0.04

        wake.set()
        reason = await scheduler.wait_for_wake(interval_seconds=30, wake=wake, stop=stop)
        assert reason is WakeReason.EVENT
        assert not wake.is_set()

        wake.set()
        stop.set()
        reason = await scheduler.wait_for_wake(interval_seconds=30, wake=wake, stop=stop)
        assert reason is WakeReason.STOP

    asyncio.run(_scenario())


def test_default_policy_never_calls_a_reasoner(tmp_path: Path) -> None:
    calls = {"count": 0}

    class Probe:
        def reason(self, observation: object) -> str:
            del observation
            calls["count"] += 1
            return "should not run"

    scheduler = ScriptedScheduler([WakeReason.INTERVAL, WakeReason.INTERVAL, WakeReason.STOP])
    config = EngineConfig(data_dir=tmp_path / "beat", heartbeat_interval=1)
    engine = LifeEngine(config, scheduler=scheduler, reasoner=Probe())
    try:
        asyncio.run(engine.run())
        status = engine.heartbeat.status()
        assert status.tick_count == 2
        assert status.reasoning_count == 0
        assert calls["count"] == 0
        assert status.status is HeartbeatStatus.IDLE
        assert engine.heartbeat.ticks(limit=10)[0].reasoning_executed is False
    finally:
        engine.close()


def test_reasoner_runs_only_when_the_policy_asks(tmp_path: Path) -> None:
    calls = {"count": 0}

    class Ask:
        def decide(self, observation: object) -> ReasoningDecision:
            del observation
            return ReasoningDecision(required=True, reason="look once")

    class Probe:
        def reason(self, observation: object) -> str:
            del observation
            calls["count"] += 1
            return "looked"

    scheduler = ScriptedScheduler([WakeReason.INTERVAL, WakeReason.STOP])
    config = EngineConfig(data_dir=tmp_path / "reason", heartbeat_interval=1)
    engine = LifeEngine(config, scheduler=scheduler, reasoning_policy=Ask(), reasoner=Probe())
    try:
        asyncio.run(engine.run())
        status = engine.heartbeat.status()
        assert calls["count"] == 1
        assert status.reasoning_count == 1
        assert status.tick_count == 1
    finally:
        engine.close()


def test_missing_reasoner_skips_reasoning_without_failing(tmp_path: Path) -> None:
    class Ask:
        def decide(self, observation: object) -> ReasoningDecision:
            del observation
            return ReasoningDecision(required=True, reason="nobody home")

    scheduler = ScriptedScheduler([WakeReason.INTERVAL, WakeReason.STOP])
    config = EngineConfig(data_dir=tmp_path / "skip", heartbeat_interval=1)
    engine = LifeEngine(config, scheduler=scheduler, reasoning_policy=Ask())
    try:
        asyncio.run(engine.run())
        status = engine.heartbeat.status()
        assert status.tick_count == 1
        assert status.reasoning_count == 0
        assert status.reasoning_skipped_count == 1
        assert status.failure_count == 0
    finally:
        engine.close()


def test_reasoner_timeout_is_a_tick_failure(tmp_path: Path) -> None:
    class Ask:
        def decide(self, observation: object) -> ReasoningDecision:
            del observation
            return ReasoningDecision(required=True, reason="slow")

    class Slow:
        def reason(self, observation: object) -> str:
            del observation
            import time

            time.sleep(0.3)
            return "too late"

    scheduler = ScriptedScheduler([WakeReason.INTERVAL, WakeReason.STOP])
    config = EngineConfig(
        data_dir=tmp_path / "slow",
        heartbeat_interval=1,
        heartbeat_max_failures=3,
    )
    engine = LifeEngine(
        config,
        scheduler=scheduler,
        reasoning_policy=Ask(),
        reasoner=Slow(),
        reason_timeout=0.05,
    )
    try:
        asyncio.run(engine.run())
        status = engine.heartbeat.status()
        assert status.reasoning_count == 0
        assert status.failure_count == 1
        assert status.tick_count == 0
        assert status.status is not HeartbeatStatus.FAILED
    finally:
        engine.close()


def test_sleep_skips_interval_ticks_until_wake(tmp_path: Path) -> None:
    scheduler = ScriptedScheduler([WakeReason.INTERVAL, WakeReason.INTERVAL, WakeReason.STOP])
    config = EngineConfig(data_dir=tmp_path / "sleep", heartbeat_interval=1)
    engine = LifeEngine(config, scheduler=scheduler)

    def _before(index: int) -> None:
        if index == 1:
            engine.heartbeat.sleep()

    scheduler.before = _before
    try:
        asyncio.run(engine.run())
        asleep = engine.heartbeat.status()
        assert asleep.tick_count == 1
        assert asleep.skipped_count == 0
        assert asleep.status is HeartbeatStatus.ASLEEP
        engine.heartbeat.wake("resume")
        asyncio.run(engine.run())
        awake = engine.heartbeat.status()
        assert awake.tick_count == 2
        assert awake.asleep is False
        assert awake.reasoning_count == 0
    finally:
        engine.close()


def test_pause_skips_a_tick(tmp_path: Path) -> None:
    scheduler = ScriptedScheduler(
        [WakeReason.INTERVAL, WakeReason.INTERVAL, WakeReason.INTERVAL, WakeReason.STOP]
    )
    config = EngineConfig(data_dir=tmp_path / "pause", heartbeat_interval=1)
    engine = LifeEngine(config, scheduler=scheduler)

    def _before(index: int) -> None:
        if index == 1:
            engine.pause()
        if index == 2:
            engine.resume()

    scheduler.before = _before
    try:
        asyncio.run(engine.run())
        status = engine.heartbeat.status()
        assert status.tick_count == 2
        assert status.skipped_count == 1
    finally:
        engine.close()


def test_repeated_failures_stop_the_loop_and_leave_the_runtime_up(tmp_path: Path) -> None:
    class Boom:
        def decide(self, observation: object) -> ReasoningDecision:
            del observation
            raise RuntimeError("boom")

    config = EngineConfig(
        data_dir=tmp_path / "fail",
        heartbeat_interval=1,
        heartbeat_max_failures=2,
    )
    engine = LifeEngine(config, scheduler=IntervalUntilStop(), reasoning_policy=Boom())

    async def _watch() -> None:
        for _ in range(200):
            if engine.heartbeat.status().status is HeartbeatStatus.FAILED:
                assert engine.state is LifecycleState.RUNNING
                assert engine.health().status is HealthStatus.UNHEALTHY
                engine.request_stop("observed")
                return
            await asyncio.sleep(0.01)
        raise AssertionError("heartbeat did not reach the failure cap")

    async def _main() -> None:
        await asyncio.gather(engine.run(), _watch())

    try:
        asyncio.run(_main())
        status = engine.heartbeat.status()
        assert status.status is HeartbeatStatus.FAILED
        assert status.failure_count >= 2
        assert engine.state is LifecycleState.STOPPED
        report = engine.health()
        assert report.status is HealthStatus.DEGRADED
        heartbeat = [check for check in report.checks if check.name == "heartbeat"]
        assert heartbeat[0].status.value == "warn"
    finally:
        engine.close()


def test_wake_after_failure_ticks_again(tmp_path: Path) -> None:
    class Boom:
        def __init__(self) -> None:
            self.calls = 0

        def decide(self, observation: object) -> ReasoningDecision:
            del observation
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("once")
            return ReasoningDecision(required=False, reason="recovered")

    scheduler = ScriptedScheduler([WakeReason.INTERVAL, WakeReason.STOP])
    config = EngineConfig(
        data_dir=tmp_path / "retry",
        heartbeat_interval=1,
        heartbeat_max_failures=1,
    )
    policy = Boom()
    engine = LifeEngine(config, scheduler=scheduler, reasoning_policy=policy)
    try:
        asyncio.run(engine.run())
        assert engine.heartbeat.status().status is HeartbeatStatus.FAILED
        engine.heartbeat.wake("retry")
        asyncio.run(engine.run())
        status = engine.heartbeat.status()
        assert status.tick_count == 1
        assert status.failure_count == 1
        assert status.status is HeartbeatStatus.IDLE
        assert policy.calls == 2
    finally:
        engine.close()


def test_memory_store_can_host_a_heartbeat(tmp_path: Path) -> None:
    store = MemoryStateStore()
    scheduler = ScriptedScheduler([WakeReason.INTERVAL, WakeReason.STOP])
    config = EngineConfig(data_dir=tmp_path / "memory", heartbeat_interval=1)
    engine = LifeEngine(config, store=store, scheduler=scheduler)
    try:
        asyncio.run(engine.run())
        assert engine.heartbeat.status().tick_count == 1
    finally:
        engine.close()
        store.close()


def test_heartbeat_store_is_required_when_enabled(tmp_path: Path) -> None:
    store = MemoryStateStore()
    store.load_heartbeat = None  # type: ignore[method-assign]
    config = EngineConfig(data_dir=tmp_path / "nostore", heartbeat_interval=1)
    with pytest.raises(Exception, match="heartbeat persistence"):
        LifeEngine(config, store=store)
    store.close()


def test_idle_heartbeat_check_passes() -> None:
    result = heartbeat_check(None, owns_runtime=False, state=LifecycleState.STOPPED)
    assert result.status.value == "pass"
    assert result.name == "heartbeat"


def test_stale_heartbeat_warns() -> None:
    snapshot = HeartbeatSnapshot(
        engine_id="00000000-0000-0000-0000-000000000001",
        status=HeartbeatStatus.BEATING,
        interval_seconds=1,
        tick_count=1,
        last_tick_at=utcnow().replace(year=utcnow().year - 1),
        updated_at=utcnow(),
        generation=1,
    )
    result = heartbeat_check(snapshot, owns_runtime=True, state=LifecycleState.RUNNING)
    assert result.status.value == "warn"


def test_disabled_heartbeat_stays_healthy(engine: LifeEngine) -> None:
    engine.start()
    report = engine.health()
    assert report.status is HealthStatus.HEALTHY
    names = [check.name for check in report.checks]
    assert "heartbeat" in names
    with pytest.raises(Exception, match="reserved"):

        class Reserved:
            name = "heartbeat"

            def run(self, snapshot: object) -> object:
                del snapshot
                return None

        engine.register_health_check(Reserved())
