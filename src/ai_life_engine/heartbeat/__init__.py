# SPDX-License-Identifier: Apache-2.0
"""Heartbeat engine.

Periodic and event-triggered wake-ups. A tick does not call a language model
unless an application policy asks for a reasoner and one is configured.
"""

from ai_life_engine.heartbeat.health import heartbeat_check
from ai_life_engine.heartbeat.models import (
    HeartbeatObservation,
    HeartbeatSnapshot,
    HeartbeatStatus,
    HeartbeatTick,
    WakeReason,
)
from ai_life_engine.heartbeat.policy import NeverReasonPolicy, ReasoningDecision
from ai_life_engine.heartbeat.scheduler import IntervalScheduler
from ai_life_engine.heartbeat.service import Heartbeat
from ai_life_engine.heartbeat.tasks import EmptyTaskSource, TaskReport

__all__ = [
    "EmptyTaskSource",
    "Heartbeat",
    "HeartbeatObservation",
    "HeartbeatSnapshot",
    "HeartbeatStatus",
    "HeartbeatTick",
    "IntervalScheduler",
    "NeverReasonPolicy",
    "ReasoningDecision",
    "TaskReport",
    "WakeReason",
    "heartbeat_check",
]
