# SPDX-License-Identifier: Apache-2.0
"""Optional persistence for the event bus.

These methods are not part of ``StateStore``. A store without them can still
boot. Publishing an event requires them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from ai_life_engine.events.models import EngineEvent, EventStatus

EVENT_STORE_METHODS = (
    "append_event",
    "list_events",
    "list_pending_events",
    "count_pending_events",
    "mark_event",
    "latest_event",
)


class EventStore(Protocol):
    """Durable event log for one runtime identity."""

    def append_event(
        self,
        *,
        event_id: UUID,
        engine_id: UUID,
        event_type: str,
        source: str,
        payload: dict[str, str],
        created_at: datetime,
    ) -> EngineEvent: ...

    def list_events(self, engine_id: UUID, *, limit: int) -> list[EngineEvent]: ...

    def list_pending_events(self, engine_id: UUID, *, limit: int) -> list[EngineEvent]: ...

    def count_pending_events(self, engine_id: UUID) -> int: ...

    def mark_event(
        self,
        engine_id: UUID,
        event_id: UUID,
        *,
        status: EventStatus,
        handler_error: str | None,
        delivered_at: datetime,
    ) -> None: ...

    def latest_event(self, engine_id: UUID) -> EngineEvent | None: ...


def supports_events(store: object) -> bool:
    """Return True when ``store`` can persist events."""

    return all(callable(getattr(store, name, None)) for name in EVENT_STORE_METHODS)
