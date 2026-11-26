# SPDX-License-Identifier: Apache-2.0
"""Optional persistence for tool calls.

A store without these methods can still boot. A tool call requires them so
every allowed or denied call leaves a row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from ai_life_engine.agents.models import ToolCallRecord, ToolCallStatus, ToolDecision

AGENT_STORE_METHODS = (
    "append_tool_call",
    "list_tool_calls",
    "latest_tool_call",
)


class AgentStore(Protocol):
    """Durable tool-call log for one runtime identity."""

    def append_tool_call(
        self,
        *,
        call_id: UUID,
        engine_id: UUID,
        agent: str,
        tool: str,
        permission: str,
        decision: ToolDecision,
        status: ToolCallStatus,
        detail: str,
        created_at: datetime,
    ) -> ToolCallRecord: ...

    def list_tool_calls(self, engine_id: UUID, *, limit: int) -> list[ToolCallRecord]: ...

    def latest_tool_call(self, engine_id: UUID) -> ToolCallRecord | None: ...


def supports_agents(store: object) -> bool:
    """Return True when ``store`` can persist tool calls."""

    return all(callable(getattr(store, name, None)) for name in AGENT_STORE_METHODS)
