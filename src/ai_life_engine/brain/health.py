# SPDX-License-Identifier: Apache-2.0
"""Model-usage health check.

No requests passes, so a runtime that never selects a model stays healthy.
A failed call warns. It does not by itself stop the runtime.
"""

from __future__ import annotations

from ai_life_engine.brain.models import UsageRecord, UsageStatus
from ai_life_engine.core.health import CheckResult, CheckStatus


def brain_check(latest: UsageRecord | None) -> CheckResult:
    """Describe the newest model call without repeating its prompt."""

    if latest is None:
        return CheckResult(
            name="brain",
            status=CheckStatus.PASS,
            message="No model requests have been recorded.",
        )
    if latest.status is UsageStatus.ERROR:
        return CheckResult(
            name="brain",
            status=CheckStatus.WARN,
            message="The latest model request failed.",
        )
    return CheckResult(
        name="brain",
        status=CheckStatus.PASS,
        message="The latest model request succeeded.",
    )
