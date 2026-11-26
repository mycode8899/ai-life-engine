# SPDX-License-Identifier: Apache-2.0
"""Model requests, responses, and usage records.

A request is one completion. Usage is the durable account of that call. The
prompt itself is not stored.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

MODEL_USAGE_RETENTION = 200
_TOKEN_CAP = 1_000_000


class UsageStatus(StrEnum):
    """Whether a model call was recorded as a success or a failure."""

    OK = "ok"
    ERROR = "error"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class TokenUsage(BaseModel):
    """Token counts reported by a provider, or estimated when it reports none."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_tokens: int = Field(ge=0, le=_TOKEN_CAP)
    completion_tokens: int = Field(ge=0, le=_TOKEN_CAP)


class ModelRequest(BaseModel):
    """What a provider receives for one completion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: str
    system: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class ModelResponse(BaseModel):
    """Text and usage returned by one provider call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    provider: str
    model: str
    usage: TokenUsage
    finish_reason: str = "stop"


class UsageRecord(BaseModel):
    """One stored model call. The prompt is not part of this record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(ge=1)
    request_id: UUID
    engine_id: UUID
    provider: str
    model: str
    prompt_tokens: int = Field(ge=0, le=_TOKEN_CAP)
    completion_tokens: int = Field(ge=0, le=_TOKEN_CAP)
    status: UsageStatus
    error: str | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _created(cls, value: datetime) -> datetime:
        return _as_utc(value)


class UsageTotals(BaseModel):
    """Sums over the usage rows still stored for one runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requests: int = Field(ge=0)
    errors: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
