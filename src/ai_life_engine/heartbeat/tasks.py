# SPDX-License-Identifier: Apache-2.0
"""Task inspection for one heartbeat tick.

This is not a planner. The default source reports that nothing is waiting.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from ai_life_engine.core.state import RuntimeSnapshot


class TaskReport(BaseModel):
    """A bounded summary of work the heartbeat noticed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int = Field(ge=0, le=10_000)
    summary: str = Field(min_length=1, max_length=256)


class TaskSource(Protocol):
    """Inspect outstanding work. Must be fast and must not call lifecycle methods."""

    def inspect(self, snapshot: RuntimeSnapshot) -> TaskReport: ...


class EmptyTaskSource:
    """Source used when a heartbeat is built without one.

    ``LifeEngine`` supplies a source that counts pending goal steps instead.
    """

    def inspect(self, snapshot: RuntimeSnapshot) -> TaskReport:
        del snapshot
        return TaskReport(count=0, summary="No tasks are waiting.")
