# SPDX-License-Identifier: Apache-2.0
"""Lifecycle states and the public runtime snapshot."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LifecycleState(StrEnum):
    """Durable and in-process states for one runtime handle.

    Transitional states (``starting``, ``pausing``, ``resuming``, ``stopping``,
    ``recovering``) are persisted so a crash in the middle of a change can be
    detected on the next start.
    """

    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    RESUMING = "resuming"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    RECOVERING = "recovering"


class RuntimeSnapshot(BaseModel):
    """Point-in-time view of one runtime handle and its durable record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    engine_id: UUID
    name: str
    state: LifecycleState
    persisted_state: LifecycleState | None
    run_id: UUID | None
    pid: int | None
    started_at: datetime | None
    updated_at: datetime
    stopped_at: datetime | None
    clean_shutdown: bool
    recovery_required: bool
    blocked_by_live_process: bool
    recovery_count: int = Field(ge=0)
    pause_count: int = Field(ge=0)
    software_version: str
    metadata: dict[str, str]
    owns_runtime: bool
    data_dir: Path
