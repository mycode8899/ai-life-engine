# SPDX-License-Identifier: Apache-2.0
"""Records stored by a state backend.

These models are the storage contract for Phase 1. A custom
:class:`~ai_life_engine.persistence.base.StateStore` reads and writes them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_life_engine.core.state import LifecycleState
from ai_life_engine.validation import clean_metadata, clean_name, clean_reason


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class EngineRecord(BaseModel):
    """The single runtime identity stored in one database."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    engine_id: UUID
    name: str
    created_at: datetime
    updated_at: datetime
    state: LifecycleState
    run_id: UUID | None = None
    pid: int | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    clean_shutdown: bool = False
    recovery_count: int = Field(default=0, ge=0)
    pause_count: int = Field(default=0, ge=0)
    generation: int = Field(default=1, ge=1)
    software_version: str
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return clean_name(value)

    @field_validator("metadata")
    @classmethod
    def _metadata(cls, value: dict[str, str]) -> dict[str, str]:
        return clean_metadata(value)

    @field_validator("software_version")
    @classmethod
    def _version(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 32:
            raise ValueError("software_version must be 1..32 characters.")
        return value

    @field_validator("pid")
    @classmethod
    def _pid(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if type(value) is not int or value <= 0:
            raise ValueError("pid must be a positive integer.")
        return value

    @field_validator("created_at", "updated_at", "started_at", "stopped_at")
    @classmethod
    def _timestamps(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _as_utc(value)


class NewAudit(BaseModel):
    """Audit row before the store assigns an id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    engine_id: UUID
    run_id: UUID | None
    action: str
    from_state: LifecycleState | None
    to_state: LifecycleState | None
    detail: str | None
    created_at: datetime

    @field_validator("action")
    @classmethod
    def _action(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 64:
            raise ValueError("Audit action must be 1..64 characters.")
        return value

    @field_validator("detail")
    @classmethod
    def _detail(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return clean_reason(value)

    @field_validator("created_at")
    @classmethod
    def _created(cls, value: datetime) -> datetime:
        return _as_utc(value)


class AuditEntry(NewAudit):
    """One persisted audit record, oldest ids first in the database."""

    id: int = Field(ge=1)


class NewCheckpoint(BaseModel):
    """Checkpoint before the store assigns an id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    engine_id: UUID
    run_id: UUID | None
    state: LifecycleState
    reason: str
    created_at: datetime
    detail: dict[str, str] = Field(default_factory=dict)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return clean_reason(value)

    @field_validator("detail")
    @classmethod
    def _detail(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 32:
            raise ValueError("Checkpoint detail supports at most 32 entries.")
        cleaned: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, str):
                raise ValueError("Checkpoint detail must be a string mapping.")
            if not key or len(key) > 64 or len(item) > 256:
                raise ValueError("Checkpoint detail entries are too large.")
            cleaned[key] = item
        return cleaned

    @field_validator("created_at")
    @classmethod
    def _created(cls, value: datetime) -> datetime:
        return _as_utc(value)


class Checkpoint(NewCheckpoint):
    """One persisted checkpoint."""

    id: int = Field(ge=1)


class CommittedState(BaseModel):
    """Atomic result of writing the runtime record, an audit row, and a checkpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record: EngineRecord
    audit: AuditEntry
    checkpoint: Checkpoint
