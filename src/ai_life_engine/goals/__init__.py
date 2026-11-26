# SPDX-License-Identifier: Apache-2.0
"""Goals, plans, reflections, and the next action.

A plan is stored text. Reflecting on it updates that text. It does not call
a tool, and it does not call a model unless ``propose`` is used.
"""

from ai_life_engine.goals.health import goals_check
from ai_life_engine.goals.models import (
    Goal,
    GoalStatus,
    GoalSummary,
    NextAction,
    Plan,
    Reflection,
    ReflectionOutcome,
    Step,
    StepStatus,
)
from ai_life_engine.goals.service import Goals
from ai_life_engine.goals.tasks import GoalTaskSource

__all__ = [
    "Goal",
    "GoalStatus",
    "GoalSummary",
    "GoalTaskSource",
    "Goals",
    "NextAction",
    "Plan",
    "Reflection",
    "ReflectionOutcome",
    "Step",
    "StepStatus",
    "goals_check",
]
