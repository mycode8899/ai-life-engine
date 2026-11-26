# SPDX-License-Identifier: Apache-2.0
"""Optional persistence for model usage.

These methods are not part of ``StateStore``. A store without them can still
boot. A completion requires them so every call leaves a usage row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from ai_life_engine.brain.models import UsageRecord, UsageStatus, UsageTotals

MODEL_STORE_METHODS = (
    "append_usage",
    "list_usage",
    "latest_usage",
    "usage_totals",
)


class ModelStore(Protocol):
    """Durable usage log for one runtime identity."""

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
    ) -> UsageRecord: ...

    def list_usage(self, engine_id: UUID, *, limit: int) -> list[UsageRecord]: ...

    def latest_usage(self, engine_id: UUID) -> UsageRecord | None: ...

    def usage_totals(self, engine_id: UUID) -> UsageTotals: ...


def supports_models(store: object) -> bool:
    """Return True when ``store`` can persist model usage."""

    return all(callable(getattr(store, name, None)) for name in MODEL_STORE_METHODS)
