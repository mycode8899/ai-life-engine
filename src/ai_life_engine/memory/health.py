# SPDX-License-Identifier: Apache-2.0
"""Memory health check.

An empty store passes, so a runtime that never remembers stays healthy.
Recorded memory also passes. This check does not judge whether the text is
useful, and it does not call a model.
"""

from __future__ import annotations

from ai_life_engine.core.health import CheckResult, CheckStatus
from ai_life_engine.memory.models import MemoryCounts


def memory_check(counts: MemoryCounts | None) -> CheckResult:
    """Describe whether any memory has been stored."""

    if counts is None or counts.total == 0:
        return CheckResult(
            name="memory",
            status=CheckStatus.PASS,
            message="No memories have been recorded.",
        )
    return CheckResult(
        name="memory",
        status=CheckStatus.PASS,
        message="Memory is available.",
    )
