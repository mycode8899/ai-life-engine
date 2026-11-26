# SPDX-License-Identifier: Apache-2.0
"""Plugin health check.

No plugins passes. A failed setup warns. The runtime stays up.
"""

from __future__ import annotations

from ai_life_engine.core.health import CheckResult, CheckStatus
from ai_life_engine.plugins.models import PluginRecord, PluginStatus


def plugins_check(records: list[PluginRecord]) -> CheckResult:
    """Describe plugin setup without including exception tracebacks."""

    if not records:
        return CheckResult(
            name="plugins",
            status=CheckStatus.PASS,
            message="No plugins have been loaded.",
        )
    if any(record.status is PluginStatus.FAILED for record in records):
        return CheckResult(
            name="plugins",
            status=CheckStatus.WARN,
            message="A plugin failed to load.",
        )
    return CheckResult(
        name="plugins",
        status=CheckStatus.PASS,
        message="Plugins loaded.",
    )
