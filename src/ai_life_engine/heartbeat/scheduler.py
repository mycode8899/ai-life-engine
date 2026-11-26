# SPDX-License-Identifier: Apache-2.0
"""Schedulers tell the heartbeat why it woke.

``interval_seconds=None`` means sleep until an event or a stop. A scheduler
must not spin in that mode.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from ai_life_engine.heartbeat.models import WakeReason


class Scheduler(Protocol):
    """Wait until the next heartbeat, an external wake, or stop."""

    async def wait_for_wake(
        self,
        *,
        interval_seconds: float | None,
        wake: asyncio.Event,
        stop: asyncio.Event,
    ) -> WakeReason: ...


class IntervalScheduler:
    """Wait for ``interval_seconds``, or return sooner on wake or stop.

    Stop wins when both events are set. A wake is consumed so the next wait
    does not return immediately. ``interval_seconds=None`` waits until wake
    or stop.
    """

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
        stop_task = asyncio.create_task(stop.wait())
        wake_task = asyncio.create_task(wake.wait())
        try:
            await asyncio.wait(
                {stop_task, wake_task},
                timeout=interval_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (stop_task, wake_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stop_task, wake_task, return_exceptions=True)
        if stop.is_set():
            return WakeReason.STOP
        if wake.is_set():
            wake.clear()
            return WakeReason.EVENT
        return WakeReason.INTERVAL
