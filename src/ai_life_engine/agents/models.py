# SPDX-License-Identifier: Apache-2.0
"""Tool calls and agent decisions.

A tool call is one permitted invocation. The argument is not stored. A short
result or error is. An agent decision names a tool. It does not run one by
itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

TOOL_CALL_RETENTION = 200


class ToolDecision(StrEnum):
    """Whether the permission check allowed the call to run."""

    ALLOWED = "allowed"
    DENIED = "denied"


class ToolCallStatus(StrEnum):
    """How a recorded call ended."""

    OK = "ok"
    ERROR = "error"
    DENIED = "denied"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ToolInfo(BaseModel):
    """Public description of one registered tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    permission: str


class ToolCallRecord(BaseModel):
    """One stored tool call. The argument is not part of this record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(ge=1)
    call_id: UUID
    engine_id: UUID
    agent: str
    tool: str
    permission: str
    decision: ToolDecision
    status: ToolCallStatus
    detail: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _created(cls, value: datetime) -> datetime:
        return _as_utc(value)


class AgentDecision(BaseModel):
    """A tool an adapter wants to call. The runtime still checks permission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str
    argument: str


class AgentRequest(BaseModel):
    """What an adapter is allowed to see for one step.

    ``tools`` lists registered tools whose permission is currently granted.
    ``next_action`` is the pending step text, or None when nothing is waiting.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    next_action: str | None
    tools: list[str]


class AgentStep(BaseModel):
    """Result of asking the selected adapter to take one step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    executed: bool
    tool: str | None = None
    result: str | None = None
    detail: str
