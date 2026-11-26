# SPDX-License-Identifier: Apache-2.0
"""Optional persistence for memory.

These methods are not part of ``StateStore``. A store without them can still
boot. Remembering or searching requires them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from ai_life_engine.memory.models import Episode, Fact, WorkingMemory

MEMORY_STORE_METHODS = (
    "upsert_working",
    "get_working",
    "list_working",
    "delete_working",
    "clear_working",
    "count_working",
    "append_episode",
    "list_episodes",
    "count_episodes",
    "upsert_fact",
    "get_fact",
    "list_facts",
    "delete_fact",
    "count_facts",
)


class MemoryStore(Protocol):
    """Durable working, episodic, and semantic memory for one runtime."""

    def upsert_working(
        self,
        *,
        engine_id: UUID,
        key: str,
        value: str,
        payload: dict[str, str],
        updated_at: datetime,
    ) -> WorkingMemory: ...

    def get_working(self, engine_id: UUID, key: str) -> WorkingMemory | None: ...

    def list_working(self, engine_id: UUID) -> list[WorkingMemory]: ...

    def delete_working(self, engine_id: UUID, key: str) -> bool: ...

    def clear_working(self, engine_id: UUID) -> None: ...

    def count_working(self, engine_id: UUID) -> int: ...

    def append_episode(
        self,
        *,
        episode_id: UUID,
        engine_id: UUID,
        summary: str,
        source: str,
        payload: dict[str, str],
        created_at: datetime,
    ) -> Episode: ...

    def list_episodes(self, engine_id: UUID, *, limit: int) -> list[Episode]: ...

    def count_episodes(self, engine_id: UUID) -> int: ...

    def upsert_fact(
        self,
        *,
        fact_id: UUID,
        engine_id: UUID,
        key: str,
        content: str,
        payload: dict[str, str],
        created_at: datetime,
        updated_at: datetime,
    ) -> Fact: ...

    def get_fact(self, engine_id: UUID, key: str) -> Fact | None: ...

    def list_facts(self, engine_id: UUID, *, limit: int) -> list[Fact]: ...

    def delete_fact(self, engine_id: UUID, key: str) -> bool: ...

    def count_facts(self, engine_id: UUID) -> int: ...


def supports_memory(store: object) -> bool:
    """Return True when ``store`` can persist memory."""

    return all(callable(getattr(store, name, None)) for name in MEMORY_STORE_METHODS)
