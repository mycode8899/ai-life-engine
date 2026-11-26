# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from ai_life_engine import LifecycleError, LifecycleState
from ai_life_engine.core.lifecycle import ALLOWED_TRANSITIONS, Lifecycle


def test_transition_table_is_explicit() -> None:
    assert ALLOWED_TRANSITIONS == {
        LifecycleState.CREATED: frozenset({LifecycleState.STARTING, LifecycleState.RECOVERING}),
        LifecycleState.STARTING: frozenset({LifecycleState.RUNNING, LifecycleState.FAILED}),
        LifecycleState.RECOVERING: frozenset({LifecycleState.RUNNING, LifecycleState.FAILED}),
        LifecycleState.RUNNING: frozenset(
            {LifecycleState.PAUSING, LifecycleState.STOPPING, LifecycleState.FAILED}
        ),
        LifecycleState.PAUSING: frozenset({LifecycleState.PAUSED, LifecycleState.FAILED}),
        LifecycleState.PAUSED: frozenset(
            {LifecycleState.RESUMING, LifecycleState.STOPPING, LifecycleState.FAILED}
        ),
        LifecycleState.RESUMING: frozenset({LifecycleState.RUNNING, LifecycleState.FAILED}),
        LifecycleState.STOPPING: frozenset({LifecycleState.STOPPED, LifecycleState.FAILED}),
        LifecycleState.STOPPED: frozenset({LifecycleState.STARTING, LifecycleState.RECOVERING}),
        LifecycleState.FAILED: frozenset({LifecycleState.RECOVERING, LifecycleState.STOPPING}),
    }
    assert set(ALLOWED_TRANSITIONS) == set(LifecycleState)


def test_legal_run_can_pause_resume_and_stop() -> None:
    lifecycle = Lifecycle()
    for target in (
        LifecycleState.STARTING,
        LifecycleState.RUNNING,
        LifecycleState.PAUSING,
        LifecycleState.PAUSED,
        LifecycleState.RESUMING,
        LifecycleState.RUNNING,
        LifecycleState.STOPPING,
        LifecycleState.STOPPED,
    ):
        lifecycle.transition(target)
    assert lifecycle.state is LifecycleState.STOPPED


def test_illegal_transition_reports_the_current_state() -> None:
    lifecycle = Lifecycle()
    with pytest.raises(LifecycleError) as caught:
        lifecycle.transition(LifecycleState.RUNNING)
    assert caught.value.code == "invalid_transition"
    assert caught.value.state is LifecycleState.CREATED
    assert lifecycle.state is LifecycleState.CREATED


def test_failure_can_recover_or_stop() -> None:
    lifecycle = Lifecycle()
    lifecycle.transition(LifecycleState.STARTING)
    lifecycle.transition(LifecycleState.FAILED)
    assert lifecycle.can(LifecycleState.RECOVERING)
    assert lifecycle.can(LifecycleState.STOPPING)
    lifecycle.transition(LifecycleState.STOPPING)
    lifecycle.transition(LifecycleState.STOPPED)
