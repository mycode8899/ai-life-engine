# SPDX-License-Identifier: Apache-2.0
"""Heartbeat records.

A heartbeat is a wake-up, not a model call. These models are the durable
view of that loop.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_life_engine.core.state import LifecycleState

HEARTBEAT_TICK_RETENTION = 50


class HeartbeatStatus(StrEnum):
    """Durable condition of the heartbeat loop."""

    IDLE = "idle"
    BEATING = "beating"
    ASLEEP = "asleep"
    FAILED = "failed"


class WakeReason(StrEnum):
    """Why the scheduler returned."""

    INTERVAL = "interval"
    EVENT = "event"
    STOP = "stop"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class HeartbeatSnapshot(BaseModel):
    """One heartbeat row, or a synthetic idle view when none has been written."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    engine_id: UUID
    status: HeartbeatStatus
    interval_seconds: float = Field(ge=0)
    tick_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    consecutive_failures: int = Field(default=0, ge=0)
    reasoning_count: int = Field(default=0, ge=0)
    reasoning_skipped_count: int = Field(default=0, ge=0)
    last_tick_at: datetime | None = None
    last_wake_reason: str | None = None
    last_error: str | None = None
    last_detail: str | None = None
    asleep: bool = False
    recovered: bool = False
    wake_pending: bool = False
    in_progress: bool = False
    generation: int = Field(default=0, ge=0)
    updated_at: datetime

    @field_validator("last_tick_at", "updated_at")
    @classmethod
    def _timestamps(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _as_utc(value)


class HeartbeatObservation(BaseModel):
    """What a reasoning policy is allowed to see on one tick.

    This is a runtime snapshot plus the task summary. It is not a prompt and
    it does not include secrets.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    engine_id: UUID
    runtime_state: LifecycleState
    tick_count: int = Field(ge=0)
    task_count: int = Field(ge=0)
    task_summary: str
    wake_reason: WakeReason
    wake_detail: str
    asleep: bool


class NewHeartbeatTick(BaseModel):
    """A tick row before the store assigns an id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    engine_id: UUID
    tick_number: int = Field(ge=0)
    wake_reason: str
    reasoning_required: bool
    reasoning_executed: bool
    task_count: int = Field(ge=0)
    runtime_state: str
    detail: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _created(cls, value: datetime) -> datetime:
        return _as_utc(value)


class HeartbeatTick(NewHeartbeatTick):
    """One recorded tick, newest rows first when listed."""

    id: int = Field(ge=1)
