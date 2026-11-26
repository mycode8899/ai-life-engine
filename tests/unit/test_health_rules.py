# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from ai_life_engine import CheckStatus, HealthStatus, LifecycleState
from ai_life_engine.core.health import CheckResult, summarize


def _check(status: CheckStatus) -> CheckResult:
    return CheckResult(name="example", status=status, message="example")


def test_failures_outrank_warnings() -> None:
    checks = [_check(CheckStatus.WARN), _check(CheckStatus.FAIL)]
    assert (
        summarize(checks, state=LifecycleState.RUNNING, owns_runtime=True) is HealthStatus.UNHEALTHY
    )


def test_warnings_degrade_a_running_runtime() -> None:
    assert (
        summarize([_check(CheckStatus.WARN)], state=LifecycleState.RUNNING, owns_runtime=True)
        is HealthStatus.DEGRADED
    )


def test_clean_pass_reports_activity() -> None:
    passed = [_check(CheckStatus.PASS)]
    assert (
        summarize(passed, state=LifecycleState.RUNNING, owns_runtime=True) is HealthStatus.HEALTHY
    )
    assert summarize(passed, state=LifecycleState.PAUSED, owns_runtime=True) is HealthStatus.HEALTHY
    assert (
        summarize(passed, state=LifecycleState.STOPPED, owns_runtime=False) is HealthStatus.INACTIVE
    )
    assert (
        summarize(passed, state=LifecycleState.CREATED, owns_runtime=False) is HealthStatus.INACTIVE
    )
