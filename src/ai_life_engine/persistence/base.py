# SPDX-License-Identifier: Apache-2.0
"""Storage interface.

SQLite is the only backend shipped in this version. PostgreSQL and other
backends would implement :class:`StateStore` in a later phase. They are not
implemented here.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ai_life_engine.exceptions import ConfigurationError
from ai_life_engine.persistence.models import (
    AuditEntry,
    Checkpoint,
    CommittedState,
    EngineRecord,
    NewAudit,
    NewCheckpoint,
)

STORE_METHODS: tuple[str, ...] = (
    "load",
    "commit_state",
    "list_audit",
    "list_checkpoints",
    "ping",
    "close",
)


class StateStore(Protocol):
    """Durable storage for one runtime database.

    ``commit_state`` must be atomic: the record, audit row, and checkpoint are
    all written, or none of them are. ``expected_generation`` is ``None`` to
    insert the first record and an integer to update that generation.
    """

    def load(self, engine_id: UUID | None) -> EngineRecord | None: ...

    def commit_state(
        self,
        record: EngineRecord,
        *,
        expected_generation: int | None,
        audit: NewAudit,
        checkpoint: NewCheckpoint,
    ) -> CommittedState: ...

    def list_audit(self, engine_id: UUID, *, limit: int) -> list[AuditEntry]: ...

    def list_checkpoints(self, engine_id: UUID, *, limit: int) -> list[Checkpoint]: ...

    def ping(self) -> None: ...

    def close(self) -> None: ...


def ensure_store(store: object) -> None:
    """Raise ConfigurationError when an object does not look like a store."""

    missing = [name for name in STORE_METHODS if not callable(getattr(store, name, None))]
    if missing:
        joined = ", ".join(missing)
        raise ConfigurationError(f"State store is missing: {joined}.")
