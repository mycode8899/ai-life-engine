# SPDX-License-Identifier: Apache-2.0
"""Optional persistence methods for the heartbeat.

These are not part of ``StateStore``. A runtime can boot on a store that
only implements the Phase 1 contract. Setting ``heartbeat_interval`` requires
this extra surface.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ai_life_engine.heartbeat.models import HeartbeatSnapshot, HeartbeatTick, NewHeartbeatTick

HEARTBEAT_STORE_METHODS = ("load_heartbeat", "commit_heartbeat", "list_heartbeat_ticks")


class HeartbeatStore(Protocol):
    """Durable heartbeat state for one runtime identity."""

    def load_heartbeat(self, engine_id: UUID) -> HeartbeatSnapshot | None: ...

    def commit_heartbeat(
        self,
        record: HeartbeatSnapshot,
        *,
        expected_generation: int | None,
        tick: NewHeartbeatTick | None = None,
    ) -> HeartbeatSnapshot: ...

    def list_heartbeat_ticks(self, engine_id: UUID, *, limit: int) -> list[HeartbeatTick]: ...


def supports_heartbeat(store: object) -> bool:
    """Return True when ``store`` can persist heartbeat state."""

    return all(callable(getattr(store, name, None)) for name in HEARTBEAT_STORE_METHODS)
