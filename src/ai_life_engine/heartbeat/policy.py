# SPDX-License-Identifier: Apache-2.0
"""Decides whether a heartbeat tick needs reasoning.

The default decision is no. A tick must not call a reasoner unless a policy
returns ``required=True`` and the application supplied a reasoner.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from ai_life_engine.heartbeat.models import HeartbeatObservation


class ReasoningDecision(BaseModel):
    """Result of a reasoning policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required: bool
    reason: str = Field(min_length=1, max_length=256)


class ReasoningPolicy(Protocol):
    """Choose whether this tick should invoke a reasoner.

    ``decide`` runs on the runtime thread and must return quickly. It must
    not call lifecycle methods and must not perform I/O that can block the
    heartbeat.
    """

    def decide(self, observation: HeartbeatObservation) -> ReasoningDecision: ...


class Reasoner(Protocol):
    """Optional work that runs only after a policy asks for it.

    ``reason`` is called outside the runtime lock, in a worker thread, and
    is cancelled from the loop's point of view when ``reason_timeout`` elapses.
    It must not call lifecycle methods.
    """

    def reason(self, observation: HeartbeatObservation) -> str: ...


class NeverReasonPolicy:
    """The default policy. It never requests a model or a reasoner."""

    def decide(self, observation: HeartbeatObservation) -> ReasoningDecision:
        del observation
        return ReasoningDecision(required=False, reason="Reasoning is not required.")
