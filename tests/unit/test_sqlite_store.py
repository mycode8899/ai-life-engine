# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from ai_life_engine import LifecycleState, PersistenceError
from ai_life_engine.persistence import EngineRecord, NewAudit, NewCheckpoint, SqliteStateStore


def _record(engine_id=None, **overrides: object) -> EngineRecord:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "engine_id": engine_id or uuid4(),
        "name": "store",
        "created_at": now,
        "updated_at": now,
        "state": LifecycleState.STOPPED,
        "clean_shutdown": True,
        "software_version": "0.1.0",
        "metadata": {"note": 'quote "ok"'},
    }
    payload.update(overrides)
    return EngineRecord.model_validate(payload)


def _audit(record: EngineRecord) -> NewAudit:
    return NewAudit(
        engine_id=record.engine_id,
        run_id=record.run_id,
        action="engine.initialize",
        from_state=None,
        to_state=record.state,
        detail="initialize",
        created_at=record.updated_at,
    )


def _checkpoint(record: EngineRecord) -> NewCheckpoint:
    return NewCheckpoint(
        engine_id=record.engine_id,
        run_id=record.run_id,
        state=record.state,
        reason="initialize",
        created_at=record.updated_at,
        detail={"previous_state": "created"},
    )


def test_round_trip_and_conflict_are_atomic(tmp_path: Path) -> None:
    store = SqliteStateStore(tmp_path / "engine.sqlite3")
    original = _record()
    created = store.commit_state(
        original,
        expected_generation=None,
        audit=_audit(original),
        checkpoint=_checkpoint(original),
    )
    assert created.record.generation == 1
    assert store.load(None) == created.record
    updated = created.record.model_copy(
        update={"name": "renamed", "state": LifecycleState.RUNNING, "clean_shutdown": False}
    )
    store.commit_state(
        updated,
        expected_generation=1,
        audit=_audit(updated),
        checkpoint=_checkpoint(updated).model_copy(update={"state": LifecycleState.RUNNING}),
    )
    with pytest.raises(PersistenceError) as caught:
        store.commit_state(
            updated,
            expected_generation=1,
            audit=_audit(updated),
            checkpoint=_checkpoint(updated).model_copy(update={"state": LifecycleState.RUNNING}),
        )
    assert caught.value.code == "conflict"
    assert len(store.list_audit(original.engine_id, limit=10)) == 2
    assert store.load(original.engine_id).name == "renamed"  # type: ignore[union-attr]
    store.close()
    with pytest.raises(PersistenceError) as closed:
        store.ping()
    assert closed.value.code == "closed"


def test_schema_mismatch_and_corrupt_record(tmp_path: Path) -> None:
    path = tmp_path / "engine.sqlite3"
    store = SqliteStateStore(path)
    record = _record()
    store.commit_state(
        record,
        expected_generation=None,
        audit=_audit(record),
        checkpoint=_checkpoint(record),
    )
    store.close()

    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(PersistenceError) as mismatch:
        SqliteStateStore(path)
    assert mismatch.value.code == "schema_mismatch"

    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_meta SET value = '1' WHERE key = 'schema_version'")
    conn.execute("UPDATE engines SET metadata_json = '[]'")
    conn.commit()
    conn.close()
    broken = SqliteStateStore(path)
    with pytest.raises(PersistenceError) as corrupt:
        broken.load(None)
    assert corrupt.value.code == "corrupt"
    broken.close()


def test_directory_and_database_permissions(tmp_path: Path) -> None:
    if __import__("os").name != "posix":
        pytest.skip("POSIX permission bits are asserted on POSIX systems.")
    path = tmp_path / "private" / "engine.sqlite3"
    store = SqliteStateStore(path)
    store.close()
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
