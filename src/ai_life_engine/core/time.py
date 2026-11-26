# SPDX-License-Identifier: Apache-2.0
"""Clock used by the runtime. Tests can monkeypatch this module."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
