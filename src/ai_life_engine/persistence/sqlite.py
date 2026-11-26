# SPDX-License-Identifier: Apache-2.0
"""SQLite state store.

Schema version 7 keeps exactly one runtime identity in a database. That is a
deliberate constraint, not a limit of the StateStore interface. Versions 1
through 6 gain the newer tables and are marked version 7.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from ai_life_engine.agents.models import (
    TOOL_CALL_RETENTION,
    ToolCallRecord,
    ToolCallStatus,
    ToolDecision,
)
from ai_life_engine.brain.models import (
    MODEL_USAGE_RETENTION,
    UsageRecord,
    UsageStatus,
    UsageTotals,
)
from ai_life_engine.events.models import EVENT_RETENTION, EngineEvent, EventStatus
from ai_life_engine.exceptions import ConfigurationError, PersistenceError
from ai_life_engine.goals.models import (
    PLAN_HISTORY,
    REFLECTION_RETENTION,
    Goal,
    GoalStatus,
    Plan,
    PlanStatus,
    Reflection,
    ReflectionOutcome,
    Step,
    StepStatus,
)
from ai_life_engine.heartbeat.models import (
    HEARTBEAT_TICK_RETENTION,
    HeartbeatSnapshot,
    HeartbeatTick,
    NewHeartbeatTick,
)
from ai_life_engine.memory.models import (
    EPISODE_RETENTION,
    Episode,
    Fact,
    WorkingMemory,
)
from ai_life_engine.persistence.models import (
    AuditEntry,
    Checkpoint,
    CommittedState,
    EngineRecord,
    NewAudit,
    NewCheckpoint,
)
from ai_life_engine.validation import bounded_limit

SCHEMA_VERSION = "7"
_UPGRADABLE_SCHEMA_VERSIONS = frozenset({"1", "2", "3", "4", "5", "6"})

_TOOL_COLUMNS = (
    "id",
    "call_id",
    "engine_id",
    "agent",
    "tool",
    "permission",
    "decision",
    "status",
    "detail",
    "created_at",
)

_GOAL_COLUMNS = (
    "id",
    "goal_id",
    "engine_id",
    "title",
    "objective",
    "priority",
    "status",
    "created_at",
    "updated_at",
)
_PLAN_COLUMNS = (
    "id",
    "plan_id",
    "engine_id",
    "goal_id",
    "status",
    "created_at",
)
_STEP_COLUMNS = (
    "id",
    "step_id",
    "engine_id",
    "plan_id",
    "goal_id",
    "position",
    "action",
    "status",
    "created_at",
    "updated_at",
)
_REFLECTION_COLUMNS = (
    "id",
    "reflection_id",
    "engine_id",
    "goal_id",
    "step_id",
    "outcome",
    "note",
    "created_at",
)

_USAGE_COLUMNS = (
    "id",
    "request_id",
    "engine_id",
    "provider",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "status",
    "error",
    "created_at",
)

_WORKING_COLUMNS = (
    "id",
    "engine_id",
    "key",
    "value",
    "payload_json",
    "updated_at",
)
_EPISODE_COLUMNS = (
    "id",
    "episode_id",
    "engine_id",
    "summary",
    "source",
    "payload_json",
    "created_at",
)
_FACT_COLUMNS = (
    "id",
    "fact_id",
    "engine_id",
    "key",
    "content",
    "payload_json",
    "created_at",
    "updated_at",
)

_EVENT_COLUMNS = (
    "id",
    "event_id",
    "engine_id",
    "type",
    "source",
    "payload_json",
    "status",
    "handler_error",
    "created_at",
    "delivered_at",
)

_HEARTBEAT_COLUMNS = (
    "engine_id",
    "status",
    "interval_seconds",
    "tick_count",
    "skipped_count",
    "failure_count",
    "consecutive_failures",
    "reasoning_count",
    "reasoning_skipped_count",
    "last_tick_at",
    "last_wake_reason",
    "last_error",
    "last_detail",
    "asleep",
    "recovered",
    "wake_pending",
    "in_progress",
    "generation",
    "updated_at",
)

_ENGINE_COLUMNS = (
    "engine_id",
    "name",
    "created_at",
    "updated_at",
    "state",
    "run_id",
    "pid",
    "started_at",
    "stopped_at",
    "clean_shutdown",
    "recovery_count",
    "pause_count",
    "generation",
    "software_version",
    "metadata_json",
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _loads_object(raw: str) -> dict[str, str]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    cleaned: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError("Expected string keys and values.")
        cleaned[key] = item
    return cleaned


def _dumps(value: dict[str, str]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _restrict_directory(path: Path) -> None:
    try:
        os.chmod(path, 0o700)
    except OSError:
        return


def _restrict_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        return


class SqliteStateStore:
    """SQLite implementation of the Phase 1 state store."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        parent = self.path.parent
        if parent.exists() and not parent.is_dir():
            raise ConfigurationError(f"Data directory path is a file: {parent}")
        if self.path.exists() and self.path.is_dir():
            raise ConfigurationError(f"Database path is a directory: {self.path}")
        parent.mkdir(parents=True, exist_ok=True)
        _restrict_directory(parent)
        self._lock = threading.RLock()
        previous_umask = os.umask(0o077)
        try:
            self._conn: sqlite3.Connection | None = sqlite3.connect(
                self.path,
                timeout=5.0,
                isolation_level=None,
                check_same_thread=False,
            )
        finally:
            os.umask(previous_umask)
        self._conn.row_factory = sqlite3.Row
        try:
            self._prepare()
        except Exception:
            self._conn.close()
            self._conn = None
            raise
        _restrict_file(self.path)

    def load(self, engine_id: UUID | None) -> EngineRecord | None:
        with self._lock:
            conn = self._connection()
            row = conn.execute(
                f"SELECT {', '.join(_ENGINE_COLUMNS)} FROM engines WHERE singleton = 1"
            ).fetchone()
            if row is None:
                return None
            record = self._record_from_row(row)
            if engine_id is not None and record.engine_id != engine_id:
                raise ConfigurationError(
                    "The data directory already belongs to a different engine.",
                    code="engine_mismatch",
                )
            return record

    def commit_state(
        self,
        record: EngineRecord,
        *,
        expected_generation: int | None,
        audit: NewAudit,
        checkpoint: NewCheckpoint,
    ) -> CommittedState:
        if expected_generation is not None and (
            type(expected_generation) is not int or expected_generation < 1
        ):
            raise PersistenceError(
                "expected_generation must be a positive integer.",
                code="invalid",
            )
        if audit.engine_id != record.engine_id or checkpoint.engine_id != record.engine_id:
            raise PersistenceError("Audit and checkpoint must use the runtime id.", code="invalid")
        if checkpoint.state is not record.state:
            raise PersistenceError(
                "Checkpoint state must match the runtime record.",
                code="invalid",
            )
        with self._transaction() as conn:
            if expected_generation is None:
                generation = self._insert_engine(conn, record)
            else:
                generation = self._update_engine(conn, record, expected_generation)
            stored = record.model_copy(update={"generation": generation})
            audit_entry = self._insert_audit(conn, audit)
            checkpoint_entry = self._insert_checkpoint(conn, checkpoint)
            return CommittedState(record=stored, audit=audit_entry, checkpoint=checkpoint_entry)

    def list_audit(self, engine_id: UUID, *, limit: int) -> list[AuditEntry]:
        bounded = self._limit(limit)
        with self._lock:
            conn = self._connection()
            rows = conn.execute(
                """
                SELECT id, engine_id, run_id, action, from_state, to_state, detail, created_at
                FROM audit_log
                WHERE engine_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(engine_id), bounded),
            ).fetchall()
        return [self._audit_from_row(row) for row in rows]

    def list_checkpoints(self, engine_id: UUID, *, limit: int) -> list[Checkpoint]:
        bounded = self._limit(limit)
        with self._lock:
            conn = self._connection()
            rows = conn.execute(
                """
                SELECT id, engine_id, run_id, state, reason, created_at, detail_json
                FROM checkpoints
                WHERE engine_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(engine_id), bounded),
            ).fetchall()
        return [self._checkpoint_from_row(row) for row in rows]

    def load_heartbeat(self, engine_id: UUID) -> HeartbeatSnapshot | None:
        with self._lock:
            conn = self._connection()
            row = conn.execute(
                f"SELECT {', '.join(_HEARTBEAT_COLUMNS)} FROM heartbeat_state WHERE singleton = 1"
            ).fetchone()
            if row is None:
                return None
            record = self._heartbeat_from_row(row)
            if record.engine_id != engine_id:
                raise ConfigurationError(
                    "The data directory already belongs to a different engine.",
                    code="engine_mismatch",
                )
            return record

    def commit_heartbeat(
        self,
        record: HeartbeatSnapshot,
        *,
        expected_generation: int | None,
        tick: NewHeartbeatTick | None = None,
    ) -> HeartbeatSnapshot:
        if expected_generation is not None and (
            type(expected_generation) is not int or expected_generation < 1
        ):
            raise PersistenceError(
                "expected_generation must be a positive integer.",
                code="invalid",
            )
        if tick is not None and tick.engine_id != record.engine_id:
            raise PersistenceError("Heartbeat tick must use the runtime id.", code="invalid")
        with self._transaction() as conn:
            engine = conn.execute("SELECT engine_id FROM engines WHERE singleton = 1").fetchone()
            if engine is None or engine["engine_id"] != str(record.engine_id):
                raise PersistenceError(
                    "Heartbeat state requires the runtime identity.",
                    code="missing",
                )
            generation = self._write_heartbeat(conn, record, expected_generation)
            if tick is not None:
                self._insert_tick(conn, tick)
                self._trim_ticks(conn, record.engine_id)
            stored = record.model_copy(update={"generation": generation})
            return stored

    def list_heartbeat_ticks(self, engine_id: UUID, *, limit: int) -> list[HeartbeatTick]:
        bounded = self._limit(limit)
        with self._lock:
            conn = self._connection()
            rows = conn.execute(
                """
                SELECT id, engine_id, tick_number, wake_reason, reasoning_required,
                       reasoning_executed, task_count, runtime_state, detail, created_at
                FROM heartbeat_ticks
                WHERE engine_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(engine_id), bounded),
            ).fetchall()
        return [self._tick_from_row(row) for row in rows]

    def append_event(
        self,
        *,
        event_id: UUID,
        engine_id: UUID,
        event_type: str,
        source: str,
        payload: dict[str, str],
        created_at: datetime,
    ) -> EngineEvent:
        with self._transaction() as conn:
            self._require_engine(conn, engine_id)
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO events (
                        event_id, engine_id, type, source, payload_json, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(event_id),
                        str(engine_id),
                        event_type,
                        source,
                        _dumps(payload),
                        EventStatus.PENDING.value,
                        _iso(created_at),
                    ),
                )
            except sqlite3.Error as exc:
                raise PersistenceError("Could not write an event.", code="io") from exc
            self._trim_events(conn, engine_id)
            return EngineEvent(
                id=int(cursor.lastrowid),
                event_id=event_id,
                engine_id=engine_id,
                type=event_type,
                source=source,
                payload=dict(payload),
                status=EventStatus.PENDING,
                created_at=created_at,
            )

    def list_events(self, engine_id: UUID, *, limit: int) -> list[EngineEvent]:
        bounded = self._limit(limit)
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    f"""
                SELECT {", ".join(_EVENT_COLUMNS)}
                FROM events
                WHERE engine_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                    (str(engine_id), bounded),
                )
                .fetchall()
            )
        return [self._event_from_row(row) for row in rows]

    def list_pending_events(self, engine_id: UUID, *, limit: int) -> list[EngineEvent]:
        bounded = self._limit(limit)
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    f"""
                SELECT {", ".join(_EVENT_COLUMNS)}
                FROM events
                WHERE engine_id = ? AND status = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                    (str(engine_id), EventStatus.PENDING.value, bounded),
                )
                .fetchall()
            )
        return [self._event_from_row(row) for row in rows]

    def count_pending_events(self, engine_id: UUID) -> int:
        with self._lock:
            row = (
                self._connection()
                .execute(
                    "SELECT COUNT(*) AS total FROM events WHERE engine_id = ? AND status = ?",
                    (str(engine_id), EventStatus.PENDING.value),
                )
                .fetchone()
            )
        return int(row["total"])

    def mark_event(
        self,
        engine_id: UUID,
        event_id: UUID,
        *,
        status: EventStatus,
        handler_error: str | None,
        delivered_at: datetime,
    ) -> None:
        if status is EventStatus.PENDING:
            raise PersistenceError("An event cannot be marked pending again.", code="invalid")
        with self._lock:
            try:
                self._connection().execute(
                    """
                    UPDATE events
                    SET status = ?, handler_error = ?, delivered_at = ?
                    WHERE engine_id = ? AND event_id = ? AND status = ?
                    """,
                    (
                        status.value,
                        handler_error,
                        _iso(delivered_at),
                        str(engine_id),
                        str(event_id),
                        EventStatus.PENDING.value,
                    ),
                )
            except sqlite3.Error as exc:
                raise PersistenceError("Could not update an event.", code="io") from exc

    def latest_event(self, engine_id: UUID) -> EngineEvent | None:
        with self._lock:
            row = (
                self._connection()
                .execute(
                    f"""
                SELECT {", ".join(_EVENT_COLUMNS)}
                FROM events
                WHERE engine_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                    (str(engine_id),),
                )
                .fetchone()
            )
        if row is None:
            return None
        return self._event_from_row(row)

    def upsert_working(
        self,
        *,
        engine_id: UUID,
        key: str,
        value: str,
        payload: dict[str, str],
        updated_at: datetime,
    ) -> WorkingMemory:
        with self._transaction() as conn:
            self._require_engine(conn, engine_id, what="A memory record")
            conn.execute(
                """
                INSERT INTO working_memory (engine_id, key, value, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(engine_id, key) DO UPDATE SET
                    value = excluded.value,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (str(engine_id), key, value, _dumps(payload), _iso(updated_at)),
            )
            row = conn.execute(
                f"""
                SELECT {", ".join(_WORKING_COLUMNS)}
                FROM working_memory
                WHERE engine_id = ? AND key = ?
                """,
                (str(engine_id), key),
            ).fetchone()
        if row is None:
            raise PersistenceError("Working memory was not stored.", code="io")
        return self._working_from_row(row)

    def get_working(self, engine_id: UUID, key: str) -> WorkingMemory | None:
        with self._lock:
            row = (
                self._connection()
                .execute(
                    f"""
                    SELECT {", ".join(_WORKING_COLUMNS)}
                    FROM working_memory
                    WHERE engine_id = ? AND key = ?
                    """,
                    (str(engine_id), key),
                )
                .fetchone()
            )
        if row is None:
            return None
        return self._working_from_row(row)

    def list_working(self, engine_id: UUID) -> list[WorkingMemory]:
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    f"""
                    SELECT {", ".join(_WORKING_COLUMNS)}
                    FROM working_memory
                    WHERE engine_id = ?
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (str(engine_id),),
                )
                .fetchall()
            )
        return [self._working_from_row(row) for row in rows]

    def delete_working(self, engine_id: UUID, key: str) -> bool:
        with self._lock:
            cursor = self._connection().execute(
                "DELETE FROM working_memory WHERE engine_id = ? AND key = ?",
                (str(engine_id), key),
            )
        return cursor.rowcount > 0

    def clear_working(self, engine_id: UUID) -> None:
        with self._lock:
            self._connection().execute(
                "DELETE FROM working_memory WHERE engine_id = ?",
                (str(engine_id),),
            )

    def count_working(self, engine_id: UUID) -> int:
        return self._count_memory("working_memory", engine_id)

    def append_episode(
        self,
        *,
        episode_id: UUID,
        engine_id: UUID,
        summary: str,
        source: str,
        payload: dict[str, str],
        created_at: datetime,
    ) -> Episode:
        with self._transaction() as conn:
            self._require_engine(conn, engine_id, what="A memory record")
            cursor = conn.execute(
                """
                INSERT INTO episodes (
                    episode_id, engine_id, summary, source, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(episode_id),
                    str(engine_id),
                    summary,
                    source,
                    _dumps(payload),
                    _iso(created_at),
                ),
            )
            self._trim_episodes(conn, engine_id)
            row_id = int(cursor.lastrowid or 0)
        stored = self._episode_by_id(row_id)
        if stored is None:
            raise PersistenceError("An episode was not stored.", code="io")
        return stored

    def list_episodes(self, engine_id: UUID, *, limit: int) -> list[Episode]:
        bounded = self._limit(limit)
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    f"""
                    SELECT {", ".join(_EPISODE_COLUMNS)}
                    FROM episodes
                    WHERE engine_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (str(engine_id), bounded),
                )
                .fetchall()
            )
        return [self._episode_from_row(row) for row in rows]

    def count_episodes(self, engine_id: UUID) -> int:
        return self._count_memory("episodes", engine_id)

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
    ) -> Fact:
        with self._transaction() as conn:
            self._require_engine(conn, engine_id, what="A memory record")
            conn.execute(
                """
                INSERT INTO facts (
                    fact_id, engine_id, key, content, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(engine_id, key) DO UPDATE SET
                    content = excluded.content,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    str(fact_id),
                    str(engine_id),
                    key,
                    content,
                    _dumps(payload),
                    _iso(created_at),
                    _iso(updated_at),
                ),
            )
            row = conn.execute(
                f"""
                SELECT {", ".join(_FACT_COLUMNS)}
                FROM facts
                WHERE engine_id = ? AND key = ?
                """,
                (str(engine_id), key),
            ).fetchone()
        if row is None:
            raise PersistenceError("A fact was not stored.", code="io")
        return self._fact_from_row(row)

    def get_fact(self, engine_id: UUID, key: str) -> Fact | None:
        with self._lock:
            row = (
                self._connection()
                .execute(
                    f"""
                    SELECT {", ".join(_FACT_COLUMNS)}
                    FROM facts
                    WHERE engine_id = ? AND key = ?
                    """,
                    (str(engine_id), key),
                )
                .fetchone()
            )
        if row is None:
            return None
        return self._fact_from_row(row)

    def list_facts(self, engine_id: UUID, *, limit: int) -> list[Fact]:
        bounded = self._limit(limit)
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    f"""
                    SELECT {", ".join(_FACT_COLUMNS)}
                    FROM facts
                    WHERE engine_id = ?
                    ORDER BY updated_at DESC, id DESC
                    LIMIT ?
                    """,
                    (str(engine_id), bounded),
                )
                .fetchall()
            )
        return [self._fact_from_row(row) for row in rows]

    def delete_fact(self, engine_id: UUID, key: str) -> bool:
        with self._lock:
            cursor = self._connection().execute(
                "DELETE FROM facts WHERE engine_id = ? AND key = ?",
                (str(engine_id), key),
            )
        return cursor.rowcount > 0

    def count_facts(self, engine_id: UUID) -> int:
        return self._count_memory("facts", engine_id)

    def _count_memory(self, table: str, engine_id: UUID) -> int:
        if table not in {"working_memory", "episodes", "facts"}:
            raise PersistenceError("Unknown memory table.", code="invalid")
        with self._lock:
            row = (
                self._connection()
                .execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE engine_id = ?",
                    (str(engine_id),),
                )
                .fetchone()
            )
        return int(row["n"]) if row is not None else 0

    def _episode_by_id(self, row_id: int) -> Episode | None:
        with self._lock:
            row = (
                self._connection()
                .execute(
                    f"SELECT {', '.join(_EPISODE_COLUMNS)} FROM episodes WHERE id = ?",
                    (row_id,),
                )
                .fetchone()
            )
        if row is None:
            return None
        return self._episode_from_row(row)

    def append_usage(
        self,
        *,
        request_id: UUID,
        engine_id: UUID,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        status: UsageStatus,
        error: str | None,
        created_at: datetime,
    ) -> UsageRecord:
        with self._transaction() as conn:
            self._require_engine(conn, engine_id, what="A model request")
            cursor = conn.execute(
                """
                INSERT INTO model_usage (
                    request_id, engine_id, provider, model, prompt_tokens,
                    completion_tokens, status, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(request_id),
                    str(engine_id),
                    provider,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    status.value,
                    error,
                    _iso(created_at),
                ),
            )
            self._trim_usage(conn, engine_id)
            row_id = int(cursor.lastrowid or 0)
        stored = self._usage_by_id(row_id)
        if stored is None:
            raise PersistenceError("Model usage was not stored.", code="io")
        return stored

    def list_usage(self, engine_id: UUID, *, limit: int) -> list[UsageRecord]:
        bounded = self._limit(limit)
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    f"""
                    SELECT {", ".join(_USAGE_COLUMNS)}
                    FROM model_usage
                    WHERE engine_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (str(engine_id), bounded),
                )
                .fetchall()
            )
        return [self._usage_from_row(row) for row in rows]

    def latest_usage(self, engine_id: UUID) -> UsageRecord | None:
        rows = self.list_usage(engine_id, limit=1)
        if not rows:
            return None
        return rows[0]

    def usage_totals(self, engine_id: UUID) -> UsageTotals:
        with self._lock:
            row = (
                self._connection()
                .execute(
                    """
                    SELECT
                        COUNT(*) AS requests,
                        COALESCE(SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), 0) AS errors,
                        COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                        COALESCE(SUM(completion_tokens), 0) AS completion_tokens
                    FROM model_usage
                    WHERE engine_id = ?
                    """,
                    (UsageStatus.ERROR.value, str(engine_id)),
                )
                .fetchone()
            )
        if row is None:
            return UsageTotals(requests=0, errors=0, prompt_tokens=0, completion_tokens=0)
        return UsageTotals(
            requests=int(row["requests"]),
            errors=int(row["errors"]),
            prompt_tokens=int(row["prompt_tokens"]),
            completion_tokens=int(row["completion_tokens"]),
        )

    def _usage_by_id(self, row_id: int) -> UsageRecord | None:
        with self._lock:
            row = (
                self._connection()
                .execute(
                    f"SELECT {', '.join(_USAGE_COLUMNS)} FROM model_usage WHERE id = ?",
                    (row_id,),
                )
                .fetchone()
            )
        if row is None:
            return None
        return self._usage_from_row(row)

    def _trim_usage(self, conn: sqlite3.Connection, engine_id: UUID) -> None:
        rows = conn.execute(
            """
            SELECT id FROM model_usage
            WHERE engine_id = ?
            ORDER BY id DESC
            LIMIT -1 OFFSET ?
            """,
            (str(engine_id), MODEL_USAGE_RETENTION),
        ).fetchall()
        if not rows:
            return
        conn.executemany(
            "DELETE FROM model_usage WHERE id = ?",
            [(int(row["id"]),) for row in rows],
        )

    def _usage_from_row(self, row: sqlite3.Row) -> UsageRecord:
        try:
            return UsageRecord(
                id=int(row["id"]),
                request_id=UUID(row["request_id"]),
                engine_id=UUID(row["engine_id"]),
                provider=row["provider"],
                model=row["model"],
                prompt_tokens=int(row["prompt_tokens"]),
                completion_tokens=int(row["completion_tokens"]),
                status=row["status"],
                error=row["error"],
                created_at=self._datetime(row["created_at"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise PersistenceError("A model usage record is corrupt.", code="corrupt") from exc

    def count_goals(self, engine_id: UUID) -> int:
        with self._lock:
            row = (
                self._connection()
                .execute(
                    "SELECT COUNT(*) AS n FROM goals WHERE engine_id = ?",
                    (str(engine_id),),
                )
                .fetchone()
            )
        return int(row["n"]) if row is not None else 0

    def add_goal(
        self,
        *,
        goal_id: UUID,
        engine_id: UUID,
        title: str,
        objective: str,
        priority: int,
        status: GoalStatus,
        created_at: datetime,
    ) -> Goal:
        stamp = _iso(created_at)
        with self._transaction() as conn:
            self._require_engine(conn, engine_id, what="A goal")
            cursor = conn.execute(
                """
                INSERT INTO goals (
                    goal_id, engine_id, title, objective, priority, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(goal_id),
                    str(engine_id),
                    title,
                    objective,
                    priority,
                    status.value,
                    stamp,
                    stamp,
                ),
            )
            row_id = int(cursor.lastrowid or 0)
        stored = self._goal_by_id(row_id)
        if stored is None:
            raise PersistenceError("A goal was not stored.", code="io")
        return stored

    def get_goal(self, engine_id: UUID, goal_id: UUID) -> Goal | None:
        with self._lock:
            row = (
                self._connection()
                .execute(
                    f"""
                    SELECT {", ".join(_GOAL_COLUMNS)}
                    FROM goals
                    WHERE engine_id = ? AND goal_id = ?
                    """,
                    (str(engine_id), str(goal_id)),
                )
                .fetchone()
            )
        if row is None:
            return None
        return self._goal_from_row(row)

    def list_goals(
        self,
        engine_id: UUID,
        *,
        status: GoalStatus | None,
        limit: int,
    ) -> list[Goal]:
        bounded = self._limit(limit)
        clauses = ["engine_id = ?"]
        params: list[object] = [str(engine_id)]
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        params.append(bounded)
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    f"""
                    SELECT {", ".join(_GOAL_COLUMNS)}
                    FROM goals
                    WHERE {" AND ".join(clauses)}
                    ORDER BY priority ASC, created_at ASC, id ASC
                    LIMIT ?
                    """,
                    params,
                )
                .fetchall()
            )
        return [self._goal_from_row(row) for row in rows]

    def update_goal_status(
        self,
        *,
        engine_id: UUID,
        goal_id: UUID,
        status: GoalStatus,
        updated_at: datetime,
    ) -> Goal:
        with self._transaction() as conn:
            self._require_engine(conn, engine_id, what="A goal")
            cursor = conn.execute(
                """
                UPDATE goals
                SET status = ?, updated_at = ?
                WHERE engine_id = ? AND goal_id = ?
                """,
                (status.value, _iso(updated_at), str(engine_id), str(goal_id)),
            )
            if cursor.rowcount != 1:
                raise PersistenceError("No goal has that id.", code="missing")
        stored = self.get_goal(engine_id, goal_id)
        if stored is None:
            raise PersistenceError("A goal was not updated.", code="io")
        return stored

    def replace_plan(
        self,
        *,
        plan_id: UUID,
        engine_id: UUID,
        goal_id: UUID,
        actions: list[str],
        created_at: datetime,
    ) -> Plan:
        stamp = _iso(created_at)
        with self._transaction() as conn:
            self._require_engine(conn, engine_id, what="A plan")
            goal = conn.execute(
                "SELECT goal_id FROM goals WHERE engine_id = ? AND goal_id = ?",
                (str(engine_id), str(goal_id)),
            ).fetchone()
            if goal is None:
                raise PersistenceError("No goal has that id.", code="missing")
            conn.execute(
                """
                UPDATE plans
                SET status = ?
                WHERE engine_id = ? AND goal_id = ? AND status = ?
                """,
                (
                    PlanStatus.SUPERSEDED.value,
                    str(engine_id),
                    str(goal_id),
                    PlanStatus.ACCEPTED.value,
                ),
            )
            cursor = conn.execute(
                """
                INSERT INTO plans (plan_id, engine_id, goal_id, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(plan_id),
                    str(engine_id),
                    str(goal_id),
                    PlanStatus.ACCEPTED.value,
                    stamp,
                ),
            )
            for position, action in enumerate(actions, start=1):
                conn.execute(
                    """
                    INSERT INTO plan_steps (
                        step_id, engine_id, plan_id, goal_id, position, action,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        str(engine_id),
                        str(plan_id),
                        str(goal_id),
                        position,
                        action,
                        StepStatus.PENDING.value,
                        stamp,
                        stamp,
                    ),
                )
            self._trim_plans(conn, engine_id, goal_id)
            row_id = int(cursor.lastrowid or 0)
        stored = self._plan_by_id(row_id)
        if stored is None:
            raise PersistenceError("A plan was not stored.", code="io")
        return stored

    def accepted_plan(self, engine_id: UUID, goal_id: UUID) -> Plan | None:
        with self._lock:
            row = (
                self._connection()
                .execute(
                    f"""
                    SELECT {", ".join(_PLAN_COLUMNS)}
                    FROM plans
                    WHERE engine_id = ? AND goal_id = ? AND status = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (str(engine_id), str(goal_id), PlanStatus.ACCEPTED.value),
                )
                .fetchone()
            )
        if row is None:
            return None
        return self._plan_from_row(row)

    def list_plans(self, engine_id: UUID, goal_id: UUID, *, limit: int) -> list[Plan]:
        bounded = self._limit(limit)
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    f"""
                    SELECT {", ".join(_PLAN_COLUMNS)}
                    FROM plans
                    WHERE engine_id = ? AND goal_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (str(engine_id), str(goal_id), bounded),
                )
                .fetchall()
            )
        return [self._plan_from_row(row) for row in rows]

    def list_steps(self, engine_id: UUID, plan_id: UUID) -> list[Step]:
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    f"""
                    SELECT {", ".join(_STEP_COLUMNS)}
                    FROM plan_steps
                    WHERE engine_id = ? AND plan_id = ?
                    ORDER BY position ASC
                    """,
                    (str(engine_id), str(plan_id)),
                )
                .fetchall()
            )
        return [self._step_from_row(row) for row in rows]

    def apply_reflection(
        self,
        *,
        reflection_id: UUID,
        engine_id: UUID,
        goal_id: UUID,
        goal_status: GoalStatus,
        step_id: UUID | None,
        step_status: StepStatus | None,
        outcome: ReflectionOutcome,
        note: str,
        created_at: datetime,
    ) -> Reflection:
        stamp = _iso(created_at)
        with self._transaction() as conn:
            self._require_engine(conn, engine_id, what="A reflection")
            updated = conn.execute(
                """
                UPDATE goals
                SET status = ?, updated_at = ?
                WHERE engine_id = ? AND goal_id = ?
                """,
                (goal_status.value, stamp, str(engine_id), str(goal_id)),
            )
            if updated.rowcount != 1:
                raise PersistenceError("No goal has that id.", code="missing")
            if step_id is not None and step_status is not None:
                changed = conn.execute(
                    """
                    UPDATE plan_steps
                    SET status = ?, updated_at = ?
                    WHERE engine_id = ? AND step_id = ? AND goal_id = ?
                    """,
                    (step_status.value, stamp, str(engine_id), str(step_id), str(goal_id)),
                )
                if changed.rowcount != 1:
                    raise PersistenceError("That step is not on this goal.", code="missing")
            cursor = conn.execute(
                """
                INSERT INTO reflections (
                    reflection_id, engine_id, goal_id, step_id, outcome, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(reflection_id),
                    str(engine_id),
                    str(goal_id),
                    None if step_id is None else str(step_id),
                    outcome.value,
                    note,
                    stamp,
                ),
            )
            self._trim_reflections(conn, engine_id)
            row_id = int(cursor.lastrowid or 0)
        stored = self._reflection_by_id(row_id)
        if stored is None:
            raise PersistenceError("A reflection was not stored.", code="io")
        return stored

    def list_reflections(
        self,
        engine_id: UUID,
        goal_id: UUID | None,
        *,
        limit: int,
    ) -> list[Reflection]:
        bounded = self._limit(limit)
        clauses = ["engine_id = ?"]
        params: list[object] = [str(engine_id)]
        if goal_id is not None:
            clauses.append("goal_id = ?")
            params.append(str(goal_id))
        params.append(bounded)
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    f"""
                    SELECT {", ".join(_REFLECTION_COLUMNS)}
                    FROM reflections
                    WHERE {" AND ".join(clauses)}
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    params,
                )
                .fetchall()
            )
        return [self._reflection_from_row(row) for row in rows]

    def _goal_by_id(self, row_id: int) -> Goal | None:
        return self._one("goals", _GOAL_COLUMNS, row_id, self._goal_from_row)

    def _plan_by_id(self, row_id: int) -> Plan | None:
        return self._one("plans", _PLAN_COLUMNS, row_id, self._plan_from_row)

    def _reflection_by_id(self, row_id: int) -> Reflection | None:
        return self._one("reflections", _REFLECTION_COLUMNS, row_id, self._reflection_from_row)

    def _one(self, table: str, columns: tuple[str, ...], row_id: int, build: object) -> object:
        with self._lock:
            row = (
                self._connection()
                .execute(
                    f"SELECT {', '.join(columns)} FROM {table} WHERE id = ?",
                    (row_id,),
                )
                .fetchone()
            )
        if row is None:
            return None
        return build(row)

    def _trim_plans(self, conn: sqlite3.Connection, engine_id: UUID, goal_id: UUID) -> None:
        rows = conn.execute(
            """
            SELECT plan_id FROM plans
            WHERE engine_id = ? AND goal_id = ?
            ORDER BY id DESC
            LIMIT -1 OFFSET ?
            """,
            (str(engine_id), str(goal_id), PLAN_HISTORY),
        ).fetchall()
        for row in rows:
            conn.execute("DELETE FROM plan_steps WHERE plan_id = ?", (row["plan_id"],))
            conn.execute("DELETE FROM plans WHERE plan_id = ?", (row["plan_id"],))

    def _trim_reflections(self, conn: sqlite3.Connection, engine_id: UUID) -> None:
        rows = conn.execute(
            """
            SELECT id FROM reflections
            WHERE engine_id = ?
            ORDER BY id DESC
            LIMIT -1 OFFSET ?
            """,
            (str(engine_id), REFLECTION_RETENTION),
        ).fetchall()
        if not rows:
            return
        conn.executemany(
            "DELETE FROM reflections WHERE id = ?",
            [(int(row["id"]),) for row in rows],
        )

    def _goal_from_row(self, row: sqlite3.Row) -> Goal:
        try:
            return Goal(
                id=int(row["id"]),
                goal_id=UUID(row["goal_id"]),
                engine_id=UUID(row["engine_id"]),
                title=row["title"],
                objective=row["objective"],
                priority=int(row["priority"]),
                status=row["status"],
                created_at=self._datetime(row["created_at"]),
                updated_at=self._datetime(row["updated_at"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise PersistenceError("A goal record is corrupt.", code="corrupt") from exc

    def _plan_from_row(self, row: sqlite3.Row) -> Plan:
        try:
            with self._lock:
                count = (
                    self._connection()
                    .execute(
                        "SELECT COUNT(*) AS n FROM plan_steps WHERE plan_id = ?",
                        (row["plan_id"],),
                    )
                    .fetchone()
                )
            return Plan(
                id=int(row["id"]),
                plan_id=UUID(row["plan_id"]),
                engine_id=UUID(row["engine_id"]),
                goal_id=UUID(row["goal_id"]),
                status=row["status"],
                step_count=int(count["n"]) if count is not None else 0,
                created_at=self._datetime(row["created_at"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise PersistenceError("A plan record is corrupt.", code="corrupt") from exc

    def _step_from_row(self, row: sqlite3.Row) -> Step:
        try:
            return Step(
                id=int(row["id"]),
                step_id=UUID(row["step_id"]),
                engine_id=UUID(row["engine_id"]),
                plan_id=UUID(row["plan_id"]),
                goal_id=UUID(row["goal_id"]),
                position=int(row["position"]),
                action=row["action"],
                status=row["status"],
                created_at=self._datetime(row["created_at"]),
                updated_at=self._datetime(row["updated_at"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise PersistenceError("A plan step is corrupt.", code="corrupt") from exc

    def _reflection_from_row(self, row: sqlite3.Row) -> Reflection:
        try:
            raw_step = row["step_id"]
            return Reflection(
                id=int(row["id"]),
                reflection_id=UUID(row["reflection_id"]),
                engine_id=UUID(row["engine_id"]),
                goal_id=UUID(row["goal_id"]),
                step_id=None if raw_step is None else UUID(raw_step),
                outcome=row["outcome"],
                note=row["note"],
                created_at=self._datetime(row["created_at"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise PersistenceError("A reflection record is corrupt.", code="corrupt") from exc

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
    ) -> ToolCallRecord:
        with self._transaction() as conn:
            self._require_engine(conn, engine_id, what="A tool call")
            cursor = conn.execute(
                """
                INSERT INTO tool_calls (
                    call_id, engine_id, agent, tool, permission, decision,
                    status, detail, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(call_id),
                    str(engine_id),
                    agent,
                    tool,
                    permission,
                    decision.value,
                    status.value,
                    detail,
                    _iso(created_at),
                ),
            )
            self._trim_tool_calls(conn, engine_id)
            row_id = int(cursor.lastrowid or 0)
        stored = self._tool_call_by_id(row_id)
        if stored is None:
            raise PersistenceError("A tool call was not stored.", code="io")
        return stored

    def list_tool_calls(self, engine_id: UUID, *, limit: int) -> list[ToolCallRecord]:
        bounded = self._limit(limit)
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    f"""
                    SELECT {", ".join(_TOOL_COLUMNS)}
                    FROM tool_calls
                    WHERE engine_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (str(engine_id), bounded),
                )
                .fetchall()
            )
        return [self._tool_call_from_row(row) for row in rows]

    def latest_tool_call(self, engine_id: UUID) -> ToolCallRecord | None:
        rows = self.list_tool_calls(engine_id, limit=1)
        if not rows:
            return None
        return rows[0]

    def _tool_call_by_id(self, row_id: int) -> ToolCallRecord | None:
        with self._lock:
            row = (
                self._connection()
                .execute(
                    f"SELECT {', '.join(_TOOL_COLUMNS)} FROM tool_calls WHERE id = ?",
                    (row_id,),
                )
                .fetchone()
            )
        if row is None:
            return None
        return self._tool_call_from_row(row)

    def _trim_tool_calls(self, conn: sqlite3.Connection, engine_id: UUID) -> None:
        rows = conn.execute(
            """
            SELECT id FROM tool_calls
            WHERE engine_id = ?
            ORDER BY id DESC
            LIMIT -1 OFFSET ?
            """,
            (str(engine_id), TOOL_CALL_RETENTION),
        ).fetchall()
        if not rows:
            return
        conn.executemany(
            "DELETE FROM tool_calls WHERE id = ?",
            [(int(row["id"]),) for row in rows],
        )

    def _tool_call_from_row(self, row: sqlite3.Row) -> ToolCallRecord:
        try:
            return ToolCallRecord(
                id=int(row["id"]),
                call_id=UUID(row["call_id"]),
                engine_id=UUID(row["engine_id"]),
                agent=row["agent"],
                tool=row["tool"],
                permission=row["permission"],
                decision=row["decision"],
                status=row["status"],
                detail=row["detail"],
                created_at=self._datetime(row["created_at"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise PersistenceError("A tool call record is corrupt.", code="corrupt") from exc

    def ping(self) -> None:
        with self._lock:
            conn = self._connection()
            try:
                conn.execute("SELECT 1").fetchone()
            except sqlite3.Error as exc:
                raise PersistenceError("Database is unreachable.", code="unavailable") from exc

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _prepare(self) -> None:
        conn = self._connection()
        conn.execute("PRAGMA journal_mode=WAL").fetchone()
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=FULL")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS engines (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    engine_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    run_id TEXT,
                    pid INTEGER,
                    started_at TEXT,
                    stopped_at TEXT,
                    clean_shutdown INTEGER NOT NULL,
                    recovery_count INTEGER NOT NULL,
                    pause_count INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    software_version TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    engine_id TEXT NOT NULL,
                    run_id TEXT,
                    action TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT,
                    detail TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (engine_id) REFERENCES engines(engine_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    engine_id TEXT NOT NULL,
                    run_id TEXT,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    FOREIGN KEY (engine_id) REFERENCES engines(engine_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_engine ON audit_log(engine_id, id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_checkpoints_engine ON checkpoints(engine_id, id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS heartbeat_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    engine_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    interval_seconds REAL NOT NULL,
                    tick_count INTEGER NOT NULL,
                    skipped_count INTEGER NOT NULL,
                    failure_count INTEGER NOT NULL,
                    consecutive_failures INTEGER NOT NULL,
                    reasoning_count INTEGER NOT NULL,
                    reasoning_skipped_count INTEGER NOT NULL,
                    last_tick_at TEXT,
                    last_wake_reason TEXT,
                    last_error TEXT,
                    last_detail TEXT,
                    asleep INTEGER NOT NULL,
                    recovered INTEGER NOT NULL,
                    wake_pending INTEGER NOT NULL,
                    in_progress INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (engine_id) REFERENCES engines(engine_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS heartbeat_ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    engine_id TEXT NOT NULL,
                    tick_number INTEGER NOT NULL,
                    wake_reason TEXT NOT NULL,
                    reasoning_required INTEGER NOT NULL,
                    reasoning_executed INTEGER NOT NULL,
                    task_count INTEGER NOT NULL,
                    runtime_state TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (engine_id) REFERENCES engines(engine_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_heartbeat_ticks
                ON heartbeat_ticks(engine_id, id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    engine_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    handler_error TEXT,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT,
                    FOREIGN KEY (engine_id) REFERENCES engines(engine_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_engine
                ON events(engine_id, id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS working_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    engine_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (engine_id) REFERENCES engines(engine_id),
                    UNIQUE (engine_id, key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_id TEXT NOT NULL UNIQUE,
                    engine_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (engine_id) REFERENCES engines(engine_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_episodes_engine
                ON episodes(engine_id, id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fact_id TEXT NOT NULL UNIQUE,
                    engine_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (engine_id) REFERENCES engines(engine_id),
                    UNIQUE (engine_id, key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS model_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    engine_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL,
                    completion_tokens INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (engine_id) REFERENCES engines(engine_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_model_usage_engine
                ON model_usage(engine_id, id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    goal_id TEXT NOT NULL UNIQUE,
                    engine_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (engine_id) REFERENCES engines(engine_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL UNIQUE,
                    engine_id TEXT NOT NULL,
                    goal_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (engine_id) REFERENCES engines(engine_id),
                    FOREIGN KEY (goal_id) REFERENCES goals(goal_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS plan_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    step_id TEXT NOT NULL UNIQUE,
                    engine_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    goal_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (plan_id) REFERENCES plans(plan_id),
                    UNIQUE (plan_id, position)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reflections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reflection_id TEXT NOT NULL UNIQUE,
                    engine_id TEXT NOT NULL,
                    goal_id TEXT NOT NULL,
                    step_id TEXT,
                    outcome TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (goal_id) REFERENCES goals(goal_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_goals_engine
                ON goals(engine_id, priority, created_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_id TEXT NOT NULL UNIQUE,
                    engine_id TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (engine_id) REFERENCES engines(engine_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tool_calls_engine
                ON tool_calls(engine_id, id)
                """
            )
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                    (SCHEMA_VERSION,),
                )
            elif row["value"] in _UPGRADABLE_SCHEMA_VERSIONS:
                conn.execute(
                    "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                    (SCHEMA_VERSION,),
                )
            elif row["value"] != SCHEMA_VERSION:
                raise PersistenceError(
                    "Unsupported database schema version "
                    f"{row['value']}; this package supports version {SCHEMA_VERSION}.",
                    code="schema_mismatch",
                )

    def _insert_engine(self, conn: sqlite3.Connection, record: EngineRecord) -> int:
        try:
            conn.execute(
                """
                INSERT INTO engines (
                    singleton, engine_id, name, created_at, updated_at, state, run_id, pid,
                    started_at, stopped_at, clean_shutdown, recovery_count, pause_count,
                    generation, software_version, metadata_json
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (*self._identity_values(record), record.software_version, _dumps(record.metadata)),
            )
        except sqlite3.IntegrityError as exc:
            raise PersistenceError(
                "This data directory already contains a runtime.",
                code="conflict",
            ) from exc
        return 1

    def _update_engine(
        self,
        conn: sqlite3.Connection,
        record: EngineRecord,
        expected_generation: int,
    ) -> int:
        generation = expected_generation + 1
        cursor = conn.execute(
            """
            UPDATE engines SET
                name = ?,
                updated_at = ?,
                state = ?,
                run_id = ?,
                pid = ?,
                started_at = ?,
                stopped_at = ?,
                clean_shutdown = ?,
                recovery_count = ?,
                pause_count = ?,
                generation = ?,
                software_version = ?,
                metadata_json = ?
            WHERE singleton = 1 AND engine_id = ? AND generation = ?
            """,
            (
                record.name,
                _iso(record.updated_at),
                record.state.value,
                None if record.run_id is None else str(record.run_id),
                record.pid,
                _iso(record.started_at),
                _iso(record.stopped_at),
                1 if record.clean_shutdown else 0,
                record.recovery_count,
                record.pause_count,
                generation,
                record.software_version,
                _dumps(record.metadata),
                str(record.engine_id),
                expected_generation,
            ),
        )
        if cursor.rowcount != 1:
            raise PersistenceError(
                "The runtime state was updated by someone else.",
                code="conflict",
            )
        return generation

    def _insert_audit(self, conn: sqlite3.Connection, audit: NewAudit) -> AuditEntry:
        cursor = conn.execute(
            """
            INSERT INTO audit_log (
                engine_id, run_id, action, from_state, to_state, detail, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(audit.engine_id),
                None if audit.run_id is None else str(audit.run_id),
                audit.action,
                None if audit.from_state is None else audit.from_state.value,
                None if audit.to_state is None else audit.to_state.value,
                audit.detail,
                _iso(audit.created_at),
            ),
        )
        return AuditEntry(
            id=int(cursor.lastrowid),
            engine_id=audit.engine_id,
            run_id=audit.run_id,
            action=audit.action,
            from_state=audit.from_state,
            to_state=audit.to_state,
            detail=audit.detail,
            created_at=audit.created_at,
        )

    def _insert_checkpoint(self, conn: sqlite3.Connection, checkpoint: NewCheckpoint) -> Checkpoint:
        cursor = conn.execute(
            """
            INSERT INTO checkpoints (
                engine_id, run_id, state, reason, created_at, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(checkpoint.engine_id),
                None if checkpoint.run_id is None else str(checkpoint.run_id),
                checkpoint.state.value,
                checkpoint.reason,
                _iso(checkpoint.created_at),
                _dumps(checkpoint.detail),
            ),
        )
        return Checkpoint(
            id=int(cursor.lastrowid),
            engine_id=checkpoint.engine_id,
            run_id=checkpoint.run_id,
            state=checkpoint.state,
            reason=checkpoint.reason,
            created_at=checkpoint.created_at,
            detail=dict(checkpoint.detail),
        )

    def _identity_values(self, record: EngineRecord) -> tuple[object, ...]:
        return (
            str(record.engine_id),
            record.name,
            _iso(record.created_at),
            _iso(record.updated_at),
            record.state.value,
            None if record.run_id is None else str(record.run_id),
            record.pid,
            _iso(record.started_at),
            _iso(record.stopped_at),
            1 if record.clean_shutdown else 0,
            record.recovery_count,
            record.pause_count,
        )

    def _write_heartbeat(
        self,
        conn: sqlite3.Connection,
        record: HeartbeatSnapshot,
        expected_generation: int | None,
    ) -> int:
        values = (
            str(record.engine_id),
            record.status.value,
            record.interval_seconds,
            record.tick_count,
            record.skipped_count,
            record.failure_count,
            record.consecutive_failures,
            record.reasoning_count,
            record.reasoning_skipped_count,
            _iso(record.last_tick_at),
            record.last_wake_reason,
            record.last_error,
            record.last_detail,
            1 if record.asleep else 0,
            1 if record.recovered else 0,
            1 if record.wake_pending else 0,
            1 if record.in_progress else 0,
            _iso(record.updated_at),
        )
        try:
            if expected_generation is None:
                existing = conn.execute(
                    "SELECT generation FROM heartbeat_state WHERE singleton = 1"
                ).fetchone()
                if existing is not None:
                    raise PersistenceError(
                        "Heartbeat state was updated by someone else.",
                        code="conflict",
                    )
                conn.execute(
                    f"""
                    INSERT INTO heartbeat_state (
                        singleton, {", ".join(_HEARTBEAT_COLUMNS)}
                    ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    values,
                )
                return 1
            updated = conn.execute(
                f"""
                UPDATE heartbeat_state
                SET {", ".join(f"{column} = ?" for column in _HEARTBEAT_COLUMNS)}
                WHERE singleton = 1 AND engine_id = ? AND generation = ?
                """,
                (
                    *values[:17],
                    expected_generation + 1,
                    values[17],
                    str(record.engine_id),
                    expected_generation,
                ),
            )
        except sqlite3.Error as exc:
            raise PersistenceError("Could not write heartbeat state.", code="io") from exc
        if updated.rowcount != 1:
            raise PersistenceError(
                "Heartbeat state was updated by someone else.",
                code="conflict",
            )
        return expected_generation + 1

    def _insert_tick(self, conn: sqlite3.Connection, tick: NewHeartbeatTick) -> None:
        try:
            conn.execute(
                """
                INSERT INTO heartbeat_ticks (
                    engine_id, tick_number, wake_reason, reasoning_required,
                    reasoning_executed, task_count, runtime_state, detail, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(tick.engine_id),
                    tick.tick_number,
                    tick.wake_reason,
                    1 if tick.reasoning_required else 0,
                    1 if tick.reasoning_executed else 0,
                    tick.task_count,
                    tick.runtime_state,
                    tick.detail,
                    _iso(tick.created_at),
                ),
            )
        except sqlite3.Error as exc:
            raise PersistenceError("Could not write a heartbeat tick.", code="io") from exc

    def _trim_ticks(self, conn: sqlite3.Connection, engine_id: UUID) -> None:
        rows = conn.execute(
            """
            SELECT id FROM heartbeat_ticks
            WHERE engine_id = ?
            ORDER BY id DESC
            LIMIT -1 OFFSET ?
            """,
            (str(engine_id), HEARTBEAT_TICK_RETENTION),
        ).fetchall()
        if not rows:
            return
        conn.executemany(
            "DELETE FROM heartbeat_ticks WHERE id = ?",
            [(int(row["id"]),) for row in rows],
        )

    def _require_engine(
        self,
        conn: sqlite3.Connection,
        engine_id: UUID,
        *,
        what: str = "An event",
    ) -> None:
        row = conn.execute("SELECT engine_id FROM engines WHERE singleton = 1").fetchone()
        if row is None or row["engine_id"] != str(engine_id):
            raise PersistenceError(f"{what} requires the runtime identity.", code="missing")

    def _trim_events(self, conn: sqlite3.Connection, engine_id: UUID) -> None:
        rows = conn.execute(
            """
            SELECT id FROM events
            WHERE engine_id = ? AND status != ?
            ORDER BY id DESC
            LIMIT -1 OFFSET ?
            """,
            (str(engine_id), EventStatus.PENDING.value, EVENT_RETENTION),
        ).fetchall()
        if not rows:
            return
        conn.executemany(
            "DELETE FROM events WHERE id = ?",
            [(int(row["id"]),) for row in rows],
        )

    def _event_from_row(self, row: sqlite3.Row) -> EngineEvent:
        try:
            return EngineEvent(
                id=int(row["id"]),
                event_id=UUID(row["event_id"]),
                engine_id=UUID(row["engine_id"]),
                type=row["type"],
                source=row["source"],
                payload=_loads_object(row["payload_json"]),
                status=row["status"],
                handler_error=row["handler_error"],
                created_at=self._datetime(row["created_at"]),
                delivered_at=self._optional_datetime(row["delivered_at"]),
            )
        except (ValueError, KeyError, json.JSONDecodeError, TypeError) as exc:
            raise PersistenceError("An event record is corrupt.", code="corrupt") from exc

    def _trim_episodes(self, conn: sqlite3.Connection, engine_id: UUID) -> None:
        rows = conn.execute(
            """
            SELECT id FROM episodes
            WHERE engine_id = ?
            ORDER BY id DESC
            LIMIT -1 OFFSET ?
            """,
            (str(engine_id), EPISODE_RETENTION),
        ).fetchall()
        if not rows:
            return
        conn.executemany(
            "DELETE FROM episodes WHERE id = ?",
            [(int(row["id"]),) for row in rows],
        )

    def _working_from_row(self, row: sqlite3.Row) -> WorkingMemory:
        try:
            return WorkingMemory(
                id=int(row["id"]),
                engine_id=UUID(row["engine_id"]),
                key=row["key"],
                value=row["value"],
                payload=_loads_object(row["payload_json"]),
                updated_at=self._datetime(row["updated_at"]),
            )
        except (ValueError, KeyError, json.JSONDecodeError, TypeError) as exc:
            raise PersistenceError("A working memory record is corrupt.", code="corrupt") from exc

    def _episode_from_row(self, row: sqlite3.Row) -> Episode:
        try:
            return Episode(
                id=int(row["id"]),
                episode_id=UUID(row["episode_id"]),
                engine_id=UUID(row["engine_id"]),
                summary=row["summary"],
                source=row["source"],
                payload=_loads_object(row["payload_json"]),
                created_at=self._datetime(row["created_at"]),
            )
        except (ValueError, KeyError, json.JSONDecodeError, TypeError) as exc:
            raise PersistenceError("An episode record is corrupt.", code="corrupt") from exc

    def _fact_from_row(self, row: sqlite3.Row) -> Fact:
        try:
            return Fact(
                id=int(row["id"]),
                fact_id=UUID(row["fact_id"]),
                engine_id=UUID(row["engine_id"]),
                key=row["key"],
                content=row["content"],
                payload=_loads_object(row["payload_json"]),
                created_at=self._datetime(row["created_at"]),
                updated_at=self._datetime(row["updated_at"]),
            )
        except (ValueError, KeyError, json.JSONDecodeError, TypeError) as exc:
            raise PersistenceError("A fact record is corrupt.", code="corrupt") from exc

    def _heartbeat_from_row(self, row: sqlite3.Row) -> HeartbeatSnapshot:
        try:
            return HeartbeatSnapshot(
                engine_id=UUID(row["engine_id"]),
                status=row["status"],
                interval_seconds=float(row["interval_seconds"]),
                tick_count=int(row["tick_count"]),
                skipped_count=int(row["skipped_count"]),
                failure_count=int(row["failure_count"]),
                consecutive_failures=int(row["consecutive_failures"]),
                reasoning_count=int(row["reasoning_count"]),
                reasoning_skipped_count=int(row["reasoning_skipped_count"]),
                last_tick_at=self._optional_datetime(row["last_tick_at"]),
                last_wake_reason=row["last_wake_reason"],
                last_error=row["last_error"],
                last_detail=row["last_detail"],
                asleep=bool(row["asleep"]),
                recovered=bool(row["recovered"]),
                wake_pending=bool(row["wake_pending"]),
                in_progress=bool(row["in_progress"]),
                generation=int(row["generation"]),
                updated_at=self._datetime(row["updated_at"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise PersistenceError("The heartbeat record is corrupt.", code="corrupt") from exc

    def _tick_from_row(self, row: sqlite3.Row) -> HeartbeatTick:
        try:
            return HeartbeatTick(
                id=int(row["id"]),
                engine_id=UUID(row["engine_id"]),
                tick_number=int(row["tick_number"]),
                wake_reason=row["wake_reason"],
                reasoning_required=bool(row["reasoning_required"]),
                reasoning_executed=bool(row["reasoning_executed"]),
                task_count=int(row["task_count"]),
                runtime_state=row["runtime_state"],
                detail=row["detail"],
                created_at=self._datetime(row["created_at"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise PersistenceError("A heartbeat tick is corrupt.", code="corrupt") from exc

    def _record_from_row(self, row: sqlite3.Row) -> EngineRecord:
        try:
            return EngineRecord(
                engine_id=UUID(row["engine_id"]),
                name=row["name"],
                created_at=self._datetime(row["created_at"]),
                updated_at=self._datetime(row["updated_at"]),
                state=row["state"],
                run_id=None if row["run_id"] is None else UUID(row["run_id"]),
                pid=None if row["pid"] is None else int(row["pid"]),
                started_at=self._optional_datetime(row["started_at"]),
                stopped_at=self._optional_datetime(row["stopped_at"]),
                clean_shutdown=bool(row["clean_shutdown"]),
                recovery_count=int(row["recovery_count"]),
                pause_count=int(row["pause_count"]),
                generation=int(row["generation"]),
                software_version=row["software_version"],
                metadata=_loads_object(row["metadata_json"]),
            )
        except (ValueError, KeyError, json.JSONDecodeError, TypeError) as exc:
            raise PersistenceError("The runtime record is corrupt.", code="corrupt") from exc

    def _audit_from_row(self, row: sqlite3.Row) -> AuditEntry:
        try:
            return AuditEntry(
                id=int(row["id"]),
                engine_id=UUID(row["engine_id"]),
                run_id=None if row["run_id"] is None else UUID(row["run_id"]),
                action=row["action"],
                from_state=row["from_state"],
                to_state=row["to_state"],
                detail=row["detail"],
                created_at=self._datetime(row["created_at"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise PersistenceError("The audit log is corrupt.", code="corrupt") from exc

    def _checkpoint_from_row(self, row: sqlite3.Row) -> Checkpoint:
        try:
            return Checkpoint(
                id=int(row["id"]),
                engine_id=UUID(row["engine_id"]),
                run_id=None if row["run_id"] is None else UUID(row["run_id"]),
                state=row["state"],
                reason=row["reason"],
                created_at=self._datetime(row["created_at"]),
                detail=_loads_object(row["detail_json"]),
            )
        except (ValueError, KeyError, json.JSONDecodeError, TypeError) as exc:
            raise PersistenceError("A checkpoint is corrupt.", code="corrupt") from exc

    def _datetime(self, value: str) -> datetime:
        parsed = self._optional_datetime(value)
        if parsed is None:
            raise ValueError("Missing timestamp.")
        return parsed

    def _optional_datetime(self, value: str | None) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _limit(self, limit: int) -> int:
        try:
            return bounded_limit(limit)
        except ValueError as exc:
            raise PersistenceError(str(exc), code="invalid") from exc

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise PersistenceError("Store is closed.", code="closed")
        return self._conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise PersistenceError(
                    "Could not start a database transaction.",
                    code="io",
                ) from exc
            try:
                yield conn
            except Exception:
                self._rollback(conn)
                raise
            else:
                try:
                    conn.execute("COMMIT")
                except sqlite3.Error as exc:
                    self._rollback(conn)
                    raise PersistenceError("Could not commit runtime state.", code="io") from exc

    def _rollback(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            return
