# SPDX-License-Identifier: Apache-2.0
"""Tool-call health check.

No calls passes, so a runtime that never grants a tool stays healthy. A
denied or failed call warns. It does not by itself stop the runtime.
"""

from __future__ import annotations

from ai_life_engine.agents.models import ToolCallRecord, ToolCallStatus
from ai_life_engine.core.health import CheckResult, CheckStatus


def agents_check(latest: ToolCallRecord | None) -> CheckResult:
    """Describe the newest tool call without repeating its argument."""

    if latest is None:
        return CheckResult(
            name="agents",
            status=CheckStatus.PASS,
            message="No tool calls have been recorded.",
        )
    if latest.status is ToolCallStatus.OK:
        return CheckResult(
            name="agents",
            status=CheckStatus.PASS,
            message="The latest tool call succeeded.",
        )
    if latest.status is ToolCallStatus.DENIED:
        return CheckResult(
            name="agents",
            status=CheckStatus.WARN,
            message="The latest tool call was denied.",
        )
    return CheckResult(
        name="agents",
        status=CheckStatus.WARN,
        message="The latest tool call failed.",
    )
