# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

from ai_life_engine import LifecycleState
from ai_life_engine.core.recovery import assess
from ai_life_engine.persistence import EngineRecord


def _record(**overrides: object) -> EngineRecord:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "engine_id": uuid4(),
        "name": "recovery",
        "created_at": now,
        "updated_at": now,
        "state": LifecycleState.STOPPED,
        "clean_shutdown": True,
        "software_version": "0.1.0",
    }
    payload.update(overrides)
    return EngineRecord.model_validate(payload)


def test_missing_and_clean_records_do_not_recover() -> None:
    assert assess(None).required is False
    clean = assess(_record())
    assert clean.required is False
    assert clean.conflict is False


def test_live_pid_blocks_recovery() -> None:
    decision = assess(_record(state=LifecycleState.RUNNING, pid=os.getpid(), clean_shutdown=False))
    assert decision.conflict is True
    assert decision.required is False
    assert decision.previous_pid == os.getpid()


def test_dead_pid_and_failed_state_require_recovery(dead_pid: int) -> None:
    crashed = assess(_record(state=LifecycleState.RUNNING, pid=dead_pid, clean_shutdown=False))
    assert crashed.required is True
    assert crashed.conflict is False
    failed = assess(_record(state=LifecycleState.FAILED, clean_shutdown=False))
    assert failed.required is True
    assert failed.conflict is False
