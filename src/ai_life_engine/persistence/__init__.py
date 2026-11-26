# SPDX-License-Identifier: Apache-2.0
"""Persistence contracts and the SQLite backend."""

from ai_life_engine.persistence.base import STORE_METHODS, StateStore, ensure_store
from ai_life_engine.persistence.models import (
    AuditEntry,
    Checkpoint,
    CommittedState,
    EngineRecord,
    NewAudit,
    NewCheckpoint,
)
from ai_life_engine.persistence.sqlite import SCHEMA_VERSION, SqliteStateStore

__all__ = [
    "SCHEMA_VERSION",
    "STORE_METHODS",
    "AuditEntry",
    "Checkpoint",
    "CommittedState",
    "EngineRecord",
    "NewAudit",
    "NewCheckpoint",
    "SqliteStateStore",
    "StateStore",
    "ensure_store",
]
