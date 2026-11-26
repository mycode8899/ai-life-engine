# SPDX-License-Identifier: Apache-2.0
"""Optional persistence for goals, plans, and reflections.

A store without these methods can still boot. Goal calls then raise
``ConfigurationError``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from ai_life_engine.goals.models import (
    Goal,
    GoalStatus,
    Plan,
    Reflection,
    ReflectionOutcome,
    Step,
    StepStatus,
)

GOAL_STORE_METHODS = (
    "count_goals",
    "add_goal",
    "get_goal",
    "list_goals",
    "update_goal_status",
    "replace_plan",
    "accepted_plan",
    "list_plans",
    "list_steps",
    "apply_reflection",
    "list_reflections",
)


class GoalStore(Protocol):
    """Durable goals for one runtime identity."""

    def count_goals(self, engine_id: UUID) -> int: ...

    def add_goal(
        self,
        *,
        goal_id: UUID,
        engine_id: UUID,
        title: str,
        objective: str,
        priority: int,
        status: GoalStatus,
        created_at: datetime,
    ) -> Goal: ...

    def get_goal(self, engine_id: UUID, goal_id: UUID) -> Goal | None: ...

    def list_goals(
        self,
        engine_id: UUID,
        *,
        status: GoalStatus | None,
        limit: int,
    ) -> list[Goal]: ...

    def update_goal_status(
        self,
        *,
        engine_id: UUID,
        goal_id: UUID,
        status: GoalStatus,
        updated_at: datetime,
    ) -> Goal: ...

    def replace_plan(
        self,
        *,
        plan_id: UUID,
        engine_id: UUID,
        goal_id: UUID,
        actions: list[str],
        created_at: datetime,
    ) -> Plan: ...

    def accepted_plan(self, engine_id: UUID, goal_id: UUID) -> Plan | None: ...

    def list_plans(self, engine_id: UUID, goal_id: UUID, *, limit: int) -> list[Plan]: ...

    def list_steps(self, engine_id: UUID, plan_id: UUID) -> list[Step]: ...

    def apply_reflection(
        self,
        *,
        reflection_id: UUID,
        engine_id: UUID,
        goal_id: UUID,
        goal_status: GoalStatus,
        step_id: UUID | None,
        step_status: StepStatus | None,
        outcome: ReflectionOutcome,
        note: str,
        created_at: datetime,
    ) -> Reflection: ...

    def list_reflections(
        self,
        engine_id: UUID,
        goal_id: UUID | None,
        *,
        limit: int,
    ) -> list[Reflection]: ...


def supports_goals(store: object) -> bool:
    """Return True when ``store`` can persist goals."""

    return all(callable(getattr(store, name, None)) for name in GOAL_STORE_METHODS)
