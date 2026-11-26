# SPDX-License-Identifier: Apache-2.0
"""Health reports.

Built-in checks are pure functions of a snapshot plus a storage probe. Callers
can register extra checks. Checks must be fast and must not call lifecycle
methods.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai_life_engine.core.state import LifecycleState, RuntimeSnapshot
from ai_life_engine.core.time import utcnow

RESERVED_HEALTH_CHECKS = frozenset(
    {
        "storage",
        "lifecycle",
        "consistency",
        "heartbeat",
        "events",
        "memory",
        "brain",
        "goals",
        "agents",
        "plugins",
    }
)


class HealthStatus(StrEnum):
    """Overall result of a health report."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    INACTIVE = "inactive"


class CheckStatus(StrEnum):
    """Result of one check."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class CheckResult(BaseModel):
    """One named observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    status: CheckStatus
    message: str


class HealthReport(BaseModel):
    """Health of one runtime handle at a single instant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: HealthStatus
    engine_id: UUID
    name: str
    state: LifecycleState
    owns_runtime: bool
    recovery_required: bool
    blocked_by_live_process: bool
    uptime_seconds: float | None
    checked_at: datetime
    software_version: str
    checks: list[CheckResult] = Field(default_factory=list)


class HealthCheck(Protocol):
    """Extension point for an extra health check.

    ``name`` must be an identifier and must not be ``storage``, ``lifecycle``,
    ``consistency``, ``heartbeat``, ``events``, ``memory``, ``brain``, or
    ``goals``, ``agents``, or ``plugins``. ``run`` receives a snapshot and
    returns a :class:`CheckResult`.
    """

    @property
    def name(self) -> str: ...

    def run(self, snapshot: RuntimeSnapshot) -> CheckResult: ...


def summarize(
    checks: list[CheckResult],
    *,
    state: LifecycleState,
    owns_runtime: bool,
) -> HealthStatus:
    """Collapse check results and the handle state into one status."""

    if any(check.status is CheckStatus.FAIL for check in checks):
        return HealthStatus.UNHEALTHY
    if any(check.status is CheckStatus.WARN for check in checks):
        return HealthStatus.DEGRADED
    if owns_runtime and state in {LifecycleState.RUNNING, LifecycleState.PAUSED}:
        return HealthStatus.HEALTHY
    if state in {LifecycleState.CREATED, LifecycleState.STOPPED}:
        return HealthStatus.INACTIVE
    return HealthStatus.DEGRADED


def storage_check(*, ok: bool, error: str | None) -> CheckResult:
    if ok:
        return CheckResult(
            name="storage",
            status=CheckStatus.PASS,
            message="Database is reachable.",
        )
    detail = error or "Database is unreachable."
    return CheckResult(name="storage", status=CheckStatus.FAIL, message=detail)


def lifecycle_check(snapshot: RuntimeSnapshot) -> CheckResult:
    if snapshot.state is LifecycleState.FAILED:
        return CheckResult(
            name="lifecycle",
            status=CheckStatus.FAIL,
            message="The runtime is in the failed state.",
        )
    if snapshot.owns_runtime and snapshot.state in {LifecycleState.RUNNING, LifecycleState.PAUSED}:
        return CheckResult(
            name="lifecycle",
            status=CheckStatus.PASS,
            message="This process is running the runtime.",
        )
    idle = snapshot.state in {LifecycleState.CREATED, LifecycleState.STOPPED}
    if idle and not snapshot.owns_runtime:
        return CheckResult(
            name="lifecycle",
            status=CheckStatus.PASS,
            message="This process has not started the runtime.",
        )
    return CheckResult(
        name="lifecycle",
        status=CheckStatus.WARN,
        message=f"The runtime handle is {snapshot.state.value}.",
    )


def consistency_check(snapshot: RuntimeSnapshot, *, storage_ok: bool) -> CheckResult:
    if not storage_ok:
        return CheckResult(
            name="consistency",
            status=CheckStatus.PASS,
            message="Skipped because storage is unavailable.",
        )
    if snapshot.persisted_state is None:
        return CheckResult(
            name="consistency",
            status=CheckStatus.PASS,
            message="No runtime record has been written yet.",
        )
    if snapshot.owns_runtime and snapshot.persisted_state is not snapshot.state:
        return CheckResult(
            name="consistency",
            status=CheckStatus.FAIL,
            message="In-memory state does not match the database.",
        )
    if snapshot.owns_runtime:
        return CheckResult(
            name="consistency",
            status=CheckStatus.PASS,
            message="Persisted state matches this process.",
        )
    if snapshot.blocked_by_live_process:
        return CheckResult(
            name="consistency",
            status=CheckStatus.WARN,
            message="Another process holds the runtime.",
        )
    if snapshot.recovery_required:
        return CheckResult(
            name="consistency",
            status=CheckStatus.WARN,
            message="The last run did not shut down cleanly. Start the runtime to recover.",
        )
    if snapshot.persisted_state is LifecycleState.FAILED:
        return CheckResult(
            name="consistency",
            status=CheckStatus.FAIL,
            message="The last run failed.",
        )
    if snapshot.persisted_state is LifecycleState.STOPPED and snapshot.clean_shutdown:
        return CheckResult(
            name="consistency",
            status=CheckStatus.PASS,
            message="The last shutdown was clean.",
        )
    return CheckResult(
        name="consistency",
        status=CheckStatus.WARN,
        message="Persisted state needs attention.",
    )


def uptime_seconds(snapshot: RuntimeSnapshot, *, now: datetime | None = None) -> float | None:
    active = snapshot.state in {LifecycleState.RUNNING, LifecycleState.PAUSED}
    if not snapshot.owns_runtime or not active:
        return None
    if snapshot.started_at is None:
        return None
    current = now or utcnow()
    return round(max(0.0, (current - snapshot.started_at).total_seconds()), 3)
