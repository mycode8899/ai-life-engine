# SPDX-License-Identifier: Apache-2.0
"""Explicit lifecycle state machine.

The machine is pure: it does not touch disk, the clock, or a model provider.
"""

from __future__ import annotations

from ai_life_engine.core.state import LifecycleState
from ai_life_engine.exceptions import LifecycleError

ALLOWED_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
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

ACTIVE_STATES: frozenset[LifecycleState] = frozenset(
    {
        LifecycleState.STARTING,
        LifecycleState.RUNNING,
        LifecycleState.PAUSING,
        LifecycleState.PAUSED,
        LifecycleState.RESUMING,
        LifecycleState.STOPPING,
        LifecycleState.RECOVERING,
    }
)


class Lifecycle:
    """In-process lifecycle for a single engine handle."""

    def __init__(self, initial: LifecycleState = LifecycleState.CREATED) -> None:
        if initial not in LifecycleState:
            raise LifecycleError("Unknown lifecycle state.", code="invalid_transition")
        self.state = initial

    def can(self, target: LifecycleState) -> bool:
        return target in ALLOWED_TRANSITIONS[self.state]

    def transition(self, target: LifecycleState) -> LifecycleState:
        if not self.can(target):
            raise LifecycleError(
                f"Cannot move from {self.state.value} to {target.value}.",
                code="invalid_transition",
                state=self.state,
            )
        self.state = target
        return self.state

    def bind(self, state: LifecycleState) -> None:
        """Align a handle that does not own the runtime with durable state.

        This is not a transition. Owning handles must use :meth:`transition`.
        """

        if state not in LifecycleState:
            raise LifecycleError("Unknown lifecycle state.", code="invalid_transition")
        self.state = state
