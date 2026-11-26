# SPDX-License-Identifier: Apache-2.0
"""Memory records.

Working memory is a small key-value scratchpad. Episodes are an append-only
log. Facts are durable statements addressed by key. None of them call a
language model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_life_engine.validation import clean_metadata

WORKING_LIMIT = 32
EPISODE_RETENTION = 200
FACT_LIMIT = 200
MEMORY_TEXT_LIMIT = 500


class MemoryKind(StrEnum):
    """Which store a record or search hit came from."""

    WORKING = "working"
    EPISODE = "episode"
    FACT = "fact"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class WorkingMemory(BaseModel):
    """One working-memory entry. The key is unique per runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(ge=1)
    engine_id: UUID
    key: str
    value: str
    payload: dict[str, str] = Field(default_factory=dict)
    updated_at: datetime

    @field_validator("payload")
    @classmethod
    def _payload(cls, value: dict[str, str]) -> dict[str, str]:
        return clean_metadata(value)

    @field_validator("updated_at")
    @classmethod
    def _updated(cls, value: datetime) -> datetime:
        return _as_utc(value)


class Episode(BaseModel):
    """One episodic record. Newer rows replace older ones only by retention."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(ge=1)
    episode_id: UUID
    engine_id: UUID
    summary: str
    source: str
    payload: dict[str, str] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("payload")
    @classmethod
    def _payload(cls, value: dict[str, str]) -> dict[str, str]:
        return clean_metadata(value)

    @field_validator("created_at")
    @classmethod
    def _created(cls, value: datetime) -> datetime:
        return _as_utc(value)


class Fact(BaseModel):
    """One semantic fact. Remembering the same key replaces the content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(ge=1)
    fact_id: UUID
    engine_id: UUID
    key: str
    content: str
    payload: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @field_validator("payload")
    @classmethod
    def _payload(cls, value: dict[str, str]) -> dict[str, str]:
        return clean_metadata(value)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _timestamps(cls, value: datetime) -> datetime:
        return _as_utc(value)


class MemoryHit(BaseModel):
    """One search result. ``text`` is the value, summary, or fact content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: MemoryKind
    id: int = Field(ge=1)
    key: str | None = None
    text: str
    at: datetime
    payload: dict[str, str] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def _payload(cls, value: dict[str, str]) -> dict[str, str]:
        return clean_metadata(value)

    @field_validator("at")
    @classmethod
    def _at(cls, value: datetime) -> datetime:
        return _as_utc(value)


class MemoryCounts(BaseModel):
    """How many records each store currently holds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    working: int = Field(ge=0)
    episodes: int = Field(ge=0)
    facts: int = Field(ge=0)

    @property
    def total(self) -> int:
        return self.working + self.episodes + self.facts
