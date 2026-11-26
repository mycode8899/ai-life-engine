# SPDX-License-Identifier: Apache-2.0
"""Heartbeat view of pending goal steps.

This source counts stored steps. It does not call a model and it does not
execute an action.
"""

from __future__ import annotations

from ai_life_engine.core.state import RuntimeSnapshot
from ai_life_engine.exceptions import ConfigurationError, LifecycleError, PersistenceError
from ai_life_engine.goals.service import Goals
from ai_life_engine.heartbeat.tasks import TaskReport


class GoalTaskSource:
    """Report pending steps of active goals. Missing goal storage counts as none."""

    def __init__(self, goals: Goals) -> None:
        self._goals = goals

    def inspect(self, snapshot: RuntimeSnapshot) -> TaskReport:
        del snapshot
        try:
            pending, label = self._goals.task_view()
        except (ConfigurationError, LifecycleError, PersistenceError):
            return TaskReport(count=0, summary="No tasks are waiting.")
        if pending <= 0:
            return TaskReport(count=0, summary="No tasks are waiting.")
        text = f"{pending} goal steps are waiting."
        if label:
            text = f"{text} Next: {label}"
        return TaskReport(count=min(pending, 10_000), summary=text[:256])
