# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from ai_life_engine import (
    EngineConfig,
    EventStatus,
    HealthStatus,
    LifecycleError,
    LifeEngine,
)
from ai_life_engine.heartbeat.models import WakeReason
from tests.support.scripted import ScriptedScheduler


def test_publish_requires_an_identity_and_rejects_secrets(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "fresh"))
    try:
        with pytest.raises(LifecycleError):
            engine.events.publish("ping")
        engine.initialize()
        with pytest.raises(LifecycleError):
            engine.events.publish("ping", payload={"api_key": "nope"})
        with pytest.raises(LifecycleError):
            engine.events.publish("Bad Type")
    finally:
        engine.close()


def test_event_activates_a_run_without_a_heartbeat(tmp_path: Path) -> None:
    seen: list[str] = []
    config = EngineConfig(data_dir=tmp_path / "bus", name="bus", event_poll_seconds=0.05)
    engine = LifeEngine(config)
    engine.events.subscribe("ping", lambda event: seen.append(event.type))

    async def _publish() -> None:
        await asyncio.sleep(0.05)
        engine.events.publish("ping", source="external", payload={"from": "outside"})

    async def _stop() -> None:
        for _ in range(200):
            if seen:
                engine.request_stop("seen")
                return
            await asyncio.sleep(0.01)
        engine.request_stop("timed out")

    async def _main() -> None:
        await asyncio.gather(engine.run(), _publish(), _stop())

    try:
        asyncio.run(_main())
        assert seen == ["ping"]
        assert engine.heartbeat.status().tick_count == 0
        assert engine.events.history(limit=5)[0].status is EventStatus.DELIVERED
        assert engine.capabilities["llm"] is True
        assert engine.brain.configured is False
    finally:
        engine.close()


def test_another_handle_can_wake_the_running_engine(tmp_path: Path) -> None:
    seen: list[str] = []
    config = EngineConfig(data_dir=tmp_path / "external", name="external", event_poll_seconds=0.05)
    engine = LifeEngine(config)
    engine.events.subscribe("*", lambda event: seen.append(event.payload["from"]))

    async def _other() -> None:
        await asyncio.sleep(0.05)
        other = LifeEngine(config)
        try:
            other.events.publish("ping", source="external", payload={"from": "other-handle"})
        finally:
            other.close()

    async def _stop() -> None:
        for _ in range(200):
            if seen:
                engine.request_stop("seen")
                return
            await asyncio.sleep(0.01)
        engine.request_stop("timed out")

    async def _main() -> None:
        await asyncio.gather(engine.run(), _other(), _stop())

    try:
        asyncio.run(_main())
        assert seen == ["other-handle"]
        assert engine.heartbeat.status().tick_count == 0
    finally:
        engine.close()


def test_pending_event_is_delivered_on_the_next_run(tmp_path: Path) -> None:
    config = EngineConfig(data_dir=tmp_path / "replay", name="replay", event_poll_seconds=0.05)
    writer = LifeEngine(config)
    try:
        writer.initialize()
        writer.events.publish("ping", source="external", payload={"from": "before"})
    finally:
        writer.close()

    seen: list[str] = []
    engine = LifeEngine(config)
    engine.events.subscribe("ping", lambda event: seen.append(event.payload["from"]))

    async def _stop() -> None:
        for _ in range(200):
            if seen:
                engine.request_stop("seen")
                return
            await asyncio.sleep(0.01)
        engine.request_stop("timed out")

    async def _main() -> None:
        await asyncio.gather(engine.run(), _stop())

    try:
        asyncio.run(_main())
        assert seen == ["before"]
    finally:
        engine.close()


def test_event_wakes_a_sleeping_heartbeat_without_waiting(tmp_path: Path) -> None:
    seen: list[str] = []
    config = EngineConfig(
        data_dir=tmp_path / "wake",
        name="wake",
        heartbeat_interval=30,
        event_poll_seconds=0.05,
    )
    engine = LifeEngine(config)
    engine.events.subscribe("ping", lambda event: seen.append(event.type))

    async def _publish() -> None:
        await asyncio.sleep(0.05)
        engine.heartbeat.sleep()
        engine.events.publish("ping", source="external")

    async def _stop() -> None:
        for _ in range(200):
            status = engine.heartbeat.status()
            if seen and status.tick_count >= 1:
                engine.request_stop("seen")
                return
            await asyncio.sleep(0.01)
        engine.request_stop("timed out")

    async def _main() -> None:
        await asyncio.gather(engine.run(), _publish(), _stop())

    try:
        started = time.monotonic()
        asyncio.run(_main())
        elapsed = time.monotonic() - started
        assert seen == ["ping"]
        assert engine.heartbeat.status().tick_count >= 1
        assert engine.heartbeat.status().reasoning_count == 0
        assert elapsed < 5
    finally:
        engine.close()


def test_handler_failure_is_stored_and_the_runtime_stays_up(tmp_path: Path) -> None:
    config = EngineConfig(data_dir=tmp_path / "fail", name="fail", event_poll_seconds=0.05)
    engine = LifeEngine(config)

    def _boom(_event: object) -> None:
        raise RuntimeError("handler blew up")

    engine.events.subscribe("ping", _boom)

    async def _publish() -> None:
        await asyncio.sleep(0.02)
        engine.events.publish("ping")

    async def _stop() -> None:
        for _ in range(200):
            rows = engine.events.history(limit=1)
            if rows and rows[0].status is EventStatus.FAILED:
                assert engine.state.value == "running"
                assert engine.health().status is HealthStatus.DEGRADED
                engine.request_stop("failed")
                return
            await asyncio.sleep(0.01)
        engine.request_stop("timed out")

    async def _main() -> None:
        await asyncio.gather(engine.run(), _publish(), _stop())

    try:
        asyncio.run(_main())
        row = engine.events.history(limit=1)[0]
        assert row.status is EventStatus.FAILED
        assert row.handler_error is not None
        assert "blew up" in row.handler_error
    finally:
        engine.close()


def test_handler_timeout(tmp_path: Path) -> None:
    config = EngineConfig(
        data_dir=tmp_path / "timeout",
        name="timeout",
        event_poll_seconds=0.05,
        event_handler_timeout=0.05,
    )
    engine = LifeEngine(config)

    def _slow(_event: object) -> None:
        time.sleep(0.3)

    engine.events.subscribe("ping", _slow)

    async def _publish() -> None:
        await asyncio.sleep(0.02)
        engine.events.publish("ping")

    async def _stop() -> None:
        for _ in range(200):
            rows = engine.events.history(limit=1)
            if rows and rows[0].status is not EventStatus.PENDING:
                engine.request_stop("done")
                return
            await asyncio.sleep(0.01)
        engine.request_stop("timed out")

    async def _main() -> None:
        await asyncio.gather(engine.run(), _publish(), _stop())

    try:
        asyncio.run(_main())
        row = engine.events.history(limit=1)[0]
        assert row.status is EventStatus.FAILED
        assert row.handler_error == "Handler timed out."
    finally:
        engine.close()


def test_scripted_heartbeat_is_unchanged_without_events(tmp_path: Path) -> None:
    scheduler = ScriptedScheduler([WakeReason.INTERVAL, WakeReason.STOP])
    config = EngineConfig(data_dir=tmp_path / "quiet", heartbeat_interval=1)
    engine = LifeEngine(config, scheduler=scheduler)
    try:
        asyncio.run(engine.run())
        assert engine.heartbeat.status().tick_count == 1
        assert engine.events.history(limit=5) == []
    finally:
        engine.close()
