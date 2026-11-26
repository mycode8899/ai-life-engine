# SPDX-License-Identifier: Apache-2.0
"""Deterministic schedulers for heartbeat tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from ai_life_engine.heartbeat.models import WakeReason


class ScriptedScheduler:
    """Return a fixed sequence, unless stop or wake is already set."""

    def __init__(self, script: list[WakeReason]) -> None:
        self._script = list(script)
        self.calls = 0
        self.before: Callable[[int], None] | None = None

    async def wait_for_wake(
        self,
        *,
        interval_seconds: float | None,
        wake: asyncio.Event,
        stop: asyncio.Event,
    ) -> WakeReason:
        del interval_seconds
        if stop.is_set():
            return WakeReason.STOP
        if wake.is_set():
            wake.clear()
            return WakeReason.EVENT
        if self.before is not None:
            self.before(self.calls)
        self.calls += 1
        if stop.is_set():
            return WakeReason.STOP
        if wake.is_set():
            wake.clear()
            return WakeReason.EVENT
        if not self._script:
            return WakeReason.STOP
        return self._script.pop(0)


class IntervalUntilStop:
    """Tick immediately, and block with no interval until stop or wake."""

    async def wait_for_wake(
        self,
        *,
        interval_seconds: float | None,
        wake: asyncio.Event,
        stop: asyncio.Event,
    ) -> WakeReason:
        if stop.is_set():
            return WakeReason.STOP
        if wake.is_set():
            wake.clear()
            return WakeReason.EVENT
        if interval_seconds is None:
            await stop.wait()
            return WakeReason.STOP
        await asyncio.sleep(0)
        return WakeReason.INTERVAL
