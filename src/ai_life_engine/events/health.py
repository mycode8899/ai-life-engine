# SPDX-License-Identifier: Apache-2.0
"""Event-bus health check.

An empty log passes, so a runtime that never publishes stays healthy. A
handler failure warns. It does not by itself mark the runtime unhealthy.
"""

from __future__ import annotations

from ai_life_engine.core.health import CheckResult, CheckStatus
from ai_life_engine.events.models import EngineEvent, EventStatus


def events_check(latest: EngineEvent | None) -> CheckResult:
    """Describe the newest event without changing it."""

    if latest is None:
        return CheckResult(
            name="events",
            status=CheckStatus.PASS,
            message="No events have been recorded.",
        )
    if latest.status is EventStatus.FAILED:
        return CheckResult(
            name="events",
            status=CheckStatus.WARN,
            message="The latest event handler failed.",
        )
    if latest.status is EventStatus.PENDING:
        return CheckResult(
            name="events",
            status=CheckStatus.PASS,
            message="An event is waiting for delivery.",
        )
    return CheckResult(
        name="events",
        status=CheckStatus.PASS,
        message="The latest event was delivered.",
    )
