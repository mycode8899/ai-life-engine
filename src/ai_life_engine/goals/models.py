# SPDX-License-Identifier: Apache-2.0
"""Goals, plans, and reflections.

A goal is a durable intention. A plan is an ordered list of steps. A
reflection records what happened to one step. None of these records executes
a tool.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

GOAL_LIMIT = 50
PLAN_STEP_LIMIT = 8
PLAN_HISTORY = 5
REFLECTION_RETENTION = 200


class GoalStatus(StrEnum):
    """Whether a goal is eligible for a next action."""

    ACTIVE = "active"
    BLOCKED = "blocked"
    DONE = "done"
    DROPPED = "dropped"


class PlanStatus(StrEnum):
    """Whether a plan is the one a next action may use."""

    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"


class StepStatus(StrEnum):
    """Progress of one plan step."""

    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"


class ReflectionOutcome(StrEnum):
    """What a reflection says about the work."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    NOTED = "noted"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class Goal(BaseModel):
    """One stored intention. Priority 1 is considered before priority 5."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(ge=1)
    goal_id: UUID
    engine_id: UUID
    title: str
    objective: str
    priority: int = Field(ge=1, le=5)
    status: GoalStatus
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _times(cls, value: datetime) -> datetime:
        return _as_utc(value)


class Plan(BaseModel):
    """One ordered plan for a goal. Only an accepted plan supplies next actions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(ge=1)
    plan_id: UUID
    engine_id: UUID
    goal_id: UUID
    status: PlanStatus
    step_count: int = Field(ge=1, le=PLAN_STEP_LIMIT)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _created(cls, value: datetime) -> datetime:
        return _as_utc(value)


class Step(BaseModel):
    """One action inside a plan. The text is a description, not a command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(ge=1)
    step_id: UUID
    engine_id: UUID
    plan_id: UUID
    goal_id: UUID
    position: int = Field(ge=1, le=PLAN_STEP_LIMIT)
    action: str
    status: StepStatus
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _times(cls, value: datetime) -> datetime:
        return _as_utc(value)


class Reflection(BaseModel):
    """A note about a goal, optionally tied to one step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(ge=1)
    reflection_id: UUID
    engine_id: UUID
    goal_id: UUID
    step_id: UUID | None = None
    outcome: ReflectionOutcome
    note: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _created(cls, value: datetime) -> datetime:
        return _as_utc(value)


class NextAction(BaseModel):
    """The pending step that should be considered first."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal_id: UUID
    goal_title: str
    priority: int = Field(ge=1, le=5)
    plan_id: UUID
    step_id: UUID
    position: int = Field(ge=1, le=PLAN_STEP_LIMIT)
    action: str


class GoalSummary(BaseModel):
    """Counts used by health checks and the heartbeat task source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goals: int = Field(ge=0)
    active: int = Field(ge=0)
    blocked: int = Field(ge=0)
    unplanned: int = Field(ge=0)
    pending_steps: int = Field(ge=0)
