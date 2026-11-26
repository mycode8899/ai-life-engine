# SPDX-License-Identifier: Apache-2.0
"""Goal health check.

No goals passes. A blocked goal or an active goal without a plan warns. The
runtime stays up either way.
"""

from __future__ import annotations

from ai_life_engine.core.health import CheckResult, CheckStatus
from ai_life_engine.goals.models import GoalSummary


def goals_check(summary: GoalSummary | None) -> CheckResult:
    """Describe goal bookkeeping without repeating objective text."""

    if summary is None or summary.goals == 0:
        return CheckResult(
            name="goals",
            status=CheckStatus.PASS,
            message="No goals have been recorded.",
        )
    if summary.blocked:
        return CheckResult(
            name="goals",
            status=CheckStatus.WARN,
            message="A goal is blocked.",
        )
    if summary.unplanned:
        return CheckResult(
            name="goals",
            status=CheckStatus.WARN,
            message="An active goal has no accepted plan.",
        )
    return CheckResult(
        name="goals",
        status=CheckStatus.PASS,
        message="Active goals have accepted plans.",
    )
