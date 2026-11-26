# SPDX-License-Identifier: Apache-2.0
"""Memory API.

Remembering writes SQLite and returns. It does not wake the heartbeat and it
does not call a language model. Search is a case-insensitive substring over
the stored text.
"""

from __future__ import annotations

import re
from typing import Protocol
from uuid import UUID, uuid4

from ai_life_engine.core.time import utcnow
from ai_life_engine.exceptions import ConfigurationError, LifecycleError, PersistenceError
from ai_life_engine.memory.models import (
    EPISODE_RETENTION,
    FACT_LIMIT,
    MEMORY_TEXT_LIMIT,
    WORKING_LIMIT,
    Episode,
    Fact,
    MemoryCounts,
    MemoryHit,
    MemoryKind,
    WorkingMemory,
)
from ai_life_engine.memory.store import supports_memory
from ai_life_engine.observability import get_logger
from ai_life_engine.validation import bounded_limit, clean_metadata, clean_reason

logger = get_logger("ai_life_engine.memory")

_NAME = re.compile(r"[a-z][a-z0-9._-]{0,63}")


class MemoryHost(Protocol):
    """Narrow view of the runtime memory is allowed to touch."""

    @property
    def lock(self) -> object: ...

    @property
    def store(self) -> object: ...

    def engine_id(self) -> UUID: ...

    def ensure_open(self) -> None: ...

    def refresh(self) -> None: ...

    def has_identity(self) -> bool: ...


class Memory:
    """Working, episodic, and semantic memory for one runtime."""

    def __init__(self, host: MemoryHost) -> None:
        self._host = host

    def remember_working(
        self,
        key: str,
        value: str,
        *,
        payload: dict[str, str] | None = None,
    ) -> WorkingMemory:
        """Insert or replace one working-memory entry."""

        cleaned_key = _memory_name(key, "Working memory key")
        cleaned_value = _memory_text(value, "Working memory value")
        cleaned_payload = _payload(payload)
        with self._host.lock:
            store = self._ready()
            engine_id = self._host.engine_id()
            existing = store.get_working(engine_id, cleaned_key)
            if existing is None and store.count_working(engine_id) >= WORKING_LIMIT:
                raise LifecycleError("Working memory is full.", code="invalid_argument")
            stored = store.upsert_working(
                engine_id=engine_id,
                key=cleaned_key,
                value=cleaned_value,
                payload=cleaned_payload,
                updated_at=utcnow(),
            )
        logger.info("memory stored kind=working key=%s", cleaned_key)
        return stored

    def recall_working(self, key: str) -> WorkingMemory | None:
        """Return one working-memory entry, or None when the key is absent."""

        cleaned_key = _memory_name(key, "Working memory key")
        with self._host.lock:
            store = self._ready()
            return store.get_working(self._host.engine_id(), cleaned_key)

    def working(self) -> list[WorkingMemory]:
        """Return every working-memory entry, most recently updated first."""

        with self._host.lock:
            store = self._ready()
            return store.list_working(self._host.engine_id())

    def forget_working(self, key: str) -> None:
        """Delete one working-memory entry. A missing key raises."""

        cleaned_key = _memory_name(key, "Working memory key")
        with self._host.lock:
            store = self._ready()
            if not store.delete_working(self._host.engine_id(), cleaned_key):
                raise LifecycleError(
                    "That working memory key does not exist.",
                    code="invalid_argument",
                )

    def clear_working(self) -> None:
        """Delete every working-memory entry for this runtime."""

        with self._host.lock:
            store = self._ready()
            store.clear_working(self._host.engine_id())

    def remember_episode(
        self,
        summary: str,
        *,
        source: str = "application",
        payload: dict[str, str] | None = None,
    ) -> Episode:
        """Append one episode. The store keeps the newest rows."""

        cleaned = _memory_text(summary, "Episode summary")
        cleaned_source = _memory_name(source, "Episode source")
        cleaned_payload = _payload(payload)
        with self._host.lock:
            store = self._ready()
            stored = store.append_episode(
                episode_id=uuid4(),
                engine_id=self._host.engine_id(),
                summary=cleaned,
                source=cleaned_source,
                payload=cleaned_payload,
                created_at=utcnow(),
            )
        logger.info("memory stored kind=episode id=%s", stored.episode_id)
        return stored

    def episodes(self, *, limit: int = 20) -> list[Episode]:
        """Return recent episodes, newest first."""

        bounded = _as_limit(limit)
        with self._host.lock:
            store = self._ready()
            return store.list_episodes(self._host.engine_id(), limit=bounded)

    def remember_fact(
        self,
        key: str,
        content: str,
        *,
        payload: dict[str, str] | None = None,
    ) -> Fact:
        """Insert or replace one fact. The same key keeps its original id."""

        cleaned_key = _memory_name(key, "Fact key")
        cleaned = _memory_text(content, "Fact content")
        cleaned_payload = _payload(payload)
        now = utcnow()
        with self._host.lock:
            store = self._ready()
            engine_id = self._host.engine_id()
            existing = store.get_fact(engine_id, cleaned_key)
            if existing is None and store.count_facts(engine_id) >= FACT_LIMIT:
                raise LifecycleError("Semantic memory is full.", code="invalid_argument")
            fact_id = existing.fact_id if existing is not None else uuid4()
            created_at = existing.created_at if existing is not None else now
            stored = store.upsert_fact(
                fact_id=fact_id,
                engine_id=engine_id,
                key=cleaned_key,
                content=cleaned,
                payload=cleaned_payload,
                created_at=created_at,
                updated_at=now,
            )
        logger.info("memory stored kind=fact key=%s", cleaned_key)
        return stored

    def recall_fact(self, key: str) -> Fact | None:
        """Return one fact, or None when the key is absent."""

        cleaned_key = _memory_name(key, "Fact key")
        with self._host.lock:
            store = self._ready()
            return store.get_fact(self._host.engine_id(), cleaned_key)

    def facts(self, *, limit: int = 20) -> list[Fact]:
        """Return facts, most recently updated first."""

        bounded = _as_limit(limit)
        with self._host.lock:
            store = self._ready()
            return store.list_facts(self._host.engine_id(), limit=bounded)

    def forget_fact(self, key: str) -> None:
        """Delete one fact. A missing key raises."""

        cleaned_key = _memory_name(key, "Fact key")
        with self._host.lock:
            store = self._ready()
            if not store.delete_fact(self._host.engine_id(), cleaned_key):
                raise LifecycleError(
                    "That fact key does not exist.",
                    code="invalid_argument",
                )

    def search(
        self,
        query: str,
        *,
        kind: MemoryKind | str | None = None,
        limit: int = 20,
    ) -> list[MemoryHit]:
        """Find records whose text contains ``query``. Matching ignores case."""

        cleaned = _memory_text(query, "Search query", limit=200).casefold()
        selected = _kind(kind)
        bounded = _as_limit(limit)
        with self._host.lock:
            store = self._ready()
            engine_id = self._host.engine_id()
            hits: list[MemoryHit] = []
            if selected is None or selected is MemoryKind.WORKING:
                for item in store.list_working(engine_id):
                    if cleaned in item.key.casefold() or cleaned in item.value.casefold():
                        hits.append(
                            MemoryHit(
                                kind=MemoryKind.WORKING,
                                id=item.id,
                                key=item.key,
                                text=item.value,
                                at=item.updated_at,
                                payload=item.payload,
                            )
                        )
            if selected is None or selected is MemoryKind.EPISODE:
                for item in store.list_episodes(engine_id, limit=EPISODE_RETENTION):
                    if cleaned in item.summary.casefold():
                        hits.append(
                            MemoryHit(
                                kind=MemoryKind.EPISODE,
                                id=item.id,
                                key=None,
                                text=item.summary,
                                at=item.created_at,
                                payload=item.payload,
                            )
                        )
            if selected is None or selected is MemoryKind.FACT:
                for item in store.list_facts(engine_id, limit=FACT_LIMIT):
                    if cleaned in item.key.casefold() or cleaned in item.content.casefold():
                        hits.append(
                            MemoryHit(
                                kind=MemoryKind.FACT,
                                id=item.id,
                                key=item.key,
                                text=item.content,
                                at=item.updated_at,
                                payload=item.payload,
                            )
                        )
        hits.sort(key=lambda item: (item.at, item.kind.value, item.id), reverse=True)
        return hits[:bounded]

    def counts(self) -> MemoryCounts:
        """Return how many records each store holds."""

        with self._host.lock:
            return self._counts_unlocked(self._ready())

    def counts_for_health(self) -> MemoryCounts | None:
        """Return counts, or None when storage cannot answer."""

        try:
            self._host.ensure_open()
            store = self._host.store
            if not supports_memory(store):
                return None
            with self._host.lock:
                if not self._host.has_identity():
                    return MemoryCounts(working=0, episodes=0, facts=0)
                return self._counts_unlocked(store)
        except PersistenceError:
            return None

    def _ready(self) -> object:
        self._host.ensure_open()
        self._host.refresh()
        if not self._host.has_identity():
            raise LifecycleError(
                "Initialize or start the runtime before using memory.",
                code="not_running",
            )
        store = self._host.store
        if not supports_memory(store):
            raise ConfigurationError("This state store does not persist memory.")
        return store

    def _counts_unlocked(self, store: object) -> MemoryCounts:
        engine_id = self._host.engine_id()
        return MemoryCounts(
            working=store.count_working(engine_id),
            episodes=store.count_episodes(engine_id),
            facts=store.count_facts(engine_id),
        )


def _memory_name(value: object, label: str) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise LifecycleError(
            f"{label} must be 1..64 lowercase characters, digits, or . _ -.",
            code="invalid_argument",
        )
    return value


def _memory_text(value: object, label: str, *, limit: int = MEMORY_TEXT_LIMIT) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise LifecycleError(
            f"{label} must be printable text of 1..{limit} characters.",
            code="invalid_argument",
        )
    try:
        return clean_reason(value, limit=limit)
    except ValueError as exc:
        raise LifecycleError(
            f"{label} must be printable text of 1..{limit} characters.",
            code="invalid_argument",
        ) from exc


def _payload(payload: dict[str, str] | None) -> dict[str, str]:
    try:
        return clean_metadata({} if payload is None else payload)
    except ValueError as exc:
        raise LifecycleError(str(exc), code="invalid_argument") from exc


def _kind(value: MemoryKind | str | None) -> MemoryKind | None:
    if value is None:
        return None
    if isinstance(value, MemoryKind):
        return value
    try:
        return MemoryKind(value)
    except ValueError as exc:
        raise LifecycleError(
            "Memory kind must be working, episode, or fact.",
            code="invalid_argument",
        ) from exc


def _as_limit(limit: int) -> int:
    try:
        return bounded_limit(limit)
    except ValueError as exc:
        raise LifecycleError(str(exc), code="invalid_argument") from exc
