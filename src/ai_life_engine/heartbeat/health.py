# SPDX-License-Identifier: Apache-2.0
"""Heartbeat health check.

An idle heartbeat passes, so a runtime that never enables the loop stays
healthy. A loop that has given up fails only while this process is running
it. A stale beat warns.
"""

from __future__ import annotations

from datetime import datetime

from ai_life_engine.core.health import CheckResult, CheckStatus
from ai_life_engine.core.state import LifecycleState
from ai_life_engine.core.time import utcnow
from ai_life_engine.heartbeat.models import HeartbeatSnapshot, HeartbeatStatus


def heartbeat_check(
    record: HeartbeatSnapshot | None,
    *,
    owns_runtime: bool,
    state: LifecycleState,
    now: datetime | None = None,
) -> CheckResult:
    """Describe the heartbeat without changing it."""

    if record is None or record.status is HeartbeatStatus.IDLE:
        return CheckResult(
            name="heartbeat",
            status=CheckStatus.PASS,
            message="Heartbeat is idle.",
        )
    if record.status is HeartbeatStatus.FAILED:
        if owns_runtime and state is LifecycleState.RUNNING:
            return CheckResult(
                name="heartbeat",
                status=CheckStatus.FAIL,
                message="Heartbeat stopped after repeated tick failures.",
            )
        return CheckResult(
            name="heartbeat",
            status=CheckStatus.WARN,
            message="Heartbeat previously stopped after repeated tick failures.",
        )
    if record.asleep or record.status is HeartbeatStatus.ASLEEP:
        return CheckResult(
            name="heartbeat",
            status=CheckStatus.PASS,
            message="Heartbeat is asleep.",
        )
    if record.last_tick_at is None:
        return CheckResult(
            name="heartbeat",
            status=CheckStatus.PASS,
            message="Heartbeat is waiting for the first tick.",
        )
    current = now or utcnow()
    age = (current - record.last_tick_at).total_seconds()
    limit = max(record.interval_seconds * 3, 0.5)
    if age > limit:
        return CheckResult(
            name="heartbeat",
            status=CheckStatus.WARN,
            message="The last heartbeat is stale.",
        )
    return CheckResult(
        name="heartbeat",
        status=CheckStatus.PASS,
        message="Heartbeat is current.",
    )
