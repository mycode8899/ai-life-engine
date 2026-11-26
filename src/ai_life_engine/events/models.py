# SPDX-License-Identifier: Apache-2.0
"""Event records.

An event is a durable notice. Delivering it may wake the runtime. It does
not call a language model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_life_engine.validation import clean_metadata

MAX_PENDING_EVENTS = 100
EVENT_RETENTION = 100


class EventStatus(StrEnum):
    """Delivery state of one stored event."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class EngineEvent(BaseModel):
    """One event stored for a runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(ge=1)
    event_id: UUID
    engine_id: UUID
    type: str
    source: str
    payload: dict[str, str] = Field(default_factory=dict)
    status: EventStatus
    handler_error: str | None = None
    created_at: datetime
    delivered_at: datetime | None = None

    @field_validator("payload")
    @classmethod
    def _payload(cls, value: dict[str, str]) -> dict[str, str]:
        return clean_metadata(value)

    @field_validator("created_at", "delivered_at")
    @classmethod
    def _timestamps(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _as_utc(value)
