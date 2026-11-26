# SPDX-License-Identifier: Apache-2.0
"""Decide whether the next start is a normal boot, a recovery, or a conflict."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ai_life_engine.core.lifecycle import ACTIVE_STATES
from ai_life_engine.core.process import process_exists
from ai_life_engine.core.state import LifecycleState
from ai_life_engine.persistence.models import EngineRecord


class RecoveryAssessment(BaseModel):
    """Result of inspecting a durable record before this process claims it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required: bool
    conflict: bool
    reason: str
    previous_state: LifecycleState | None
    previous_pid: int | None


def assess(record: EngineRecord | None) -> RecoveryAssessment:
    """Assess a persisted record without mutating it.

    Liveness is decided only by the pid stored in the record. A live pid is an
    active claim, including the uncommon case where the operating system has
    recycled that pid for an unrelated process.
    """

    if record is None:
        return RecoveryAssessment(
            required=False,
            conflict=False,
            reason="No persisted runtime.",
            previous_state=None,
            previous_pid=None,
        )
    if record.state is LifecycleState.FAILED:
        return RecoveryAssessment(
            required=True,
            conflict=False,
            reason="The previous run failed.",
            previous_state=record.state,
            previous_pid=record.pid,
        )
    if record.state not in ACTIVE_STATES:
        reason = (
            "The previous shutdown was clean."
            if record.clean_shutdown
            else "The runtime is not active."
        )
        return RecoveryAssessment(
            required=False,
            conflict=False,
            reason=reason,
            previous_state=record.state,
            previous_pid=record.pid,
        )
    if record.pid is not None and process_exists(record.pid):
        return RecoveryAssessment(
            required=False,
            conflict=True,
            reason=f"Pid {record.pid} is still running.",
            previous_state=record.state,
            previous_pid=record.pid,
        )
    return RecoveryAssessment(
        required=True,
        conflict=False,
        reason="The previous process is not running.",
        previous_state=record.state,
        previous_pid=record.pid,
    )
