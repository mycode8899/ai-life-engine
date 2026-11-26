# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os

from ai_life_engine.core.process import process_exists


def test_current_process_is_alive() -> None:
    assert process_exists(os.getpid()) is True


def test_non_positive_pids_are_not_signaled(dead_pid: int) -> None:
    assert process_exists(0) is False
    assert process_exists(-1) is False
    assert process_exists(True) is False  # type: ignore[arg-type]
    assert process_exists(dead_pid) is False
