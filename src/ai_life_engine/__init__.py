# SPDX-License-Identifier: Apache-2.0
"""AI Life Engine, a persistent runtime for AI agents.

The core runtime manages lifecycle, SQLite state, crash recovery, and health.
The heartbeat can tick without calling a language model. Events can wake that
loop without waiting for the next interval. Memory survives a restart and can
be searched without a model. A model provider can be selected without changing
the lifecycle. Goals, plans, and reflections are stored text. A next action is
not executed until a granted tool is called. No tool is granted at startup.
Plugins load only when asked, and they cannot grant themselves.
"""

from __future__ import annotations

import logging

from ai_life_engine.agents import AgentStep, EchoAgent, EchoTool, ScriptedAgent
from ai_life_engine.brain import EchoProvider, ModelResponse, TokenUsage, open_model
from ai_life_engine.core.config import EngineConfig
from ai_life_engine.core.engine import LifeEngine, runtime_capabilities
from ai_life_engine.core.health import (
    CheckResult,
    CheckStatus,
    HealthCheck,
    HealthReport,
    HealthStatus,
)
from ai_life_engine.core.state import LifecycleState, RuntimeSnapshot
from ai_life_engine.events import EngineEvent, EventStatus
from ai_life_engine.exceptions import (
    AgentError,
    ConfigurationError,
    GoalError,
    LifecycleError,
    LifeEngineError,
    ModelError,
    PersistenceError,
)
from ai_life_engine.goals import Goal, NextAction, Plan, Reflection
from ai_life_engine.heartbeat import (
    HeartbeatObservation,
    HeartbeatSnapshot,
    HeartbeatStatus,
    HeartbeatTick,
    IntervalScheduler,
    NeverReasonPolicy,
    ReasoningDecision,
    TaskReport,
    WakeReason,
)
from ai_life_engine.memory import (
    Episode,
    Fact,
    MemoryCounts,
    MemoryHit,
    MemoryKind,
    WorkingMemory,
)
from ai_life_engine.observability import configure_logging
from ai_life_engine.version import __version__

_package_logger = logging.getLogger("ai_life_engine")
if not any(isinstance(handler, logging.NullHandler) for handler in _package_logger.handlers):
    _package_logger.addHandler(logging.NullHandler())

__all__ = [
    "AgentError",
    "AgentStep",
    "CheckResult",
    "CheckStatus",
    "ConfigurationError",
    "EchoAgent",
    "EchoProvider",
    "EchoTool",
    "EngineConfig",
    "EngineEvent",
    "Episode",
    "EventStatus",
    "Fact",
    "Goal",
    "GoalError",
    "HealthCheck",
    "HealthReport",
    "HealthStatus",
    "HeartbeatObservation",
    "HeartbeatSnapshot",
    "HeartbeatStatus",
    "HeartbeatTick",
    "IntervalScheduler",
    "LifeEngine",
    "LifeEngineError",
    "LifecycleError",
    "LifecycleState",
    "MemoryCounts",
    "MemoryHit",
    "MemoryKind",
    "ModelError",
    "ModelResponse",
    "NeverReasonPolicy",
    "NextAction",
    "PersistenceError",
    "Plan",
    "ReasoningDecision",
    "Reflection",
    "RuntimeSnapshot",
    "ScriptedAgent",
    "TaskReport",
    "TokenUsage",
    "WakeReason",
    "WorkingMemory",
    "__version__",
    "configure_logging",
    "open_model",
    "runtime_capabilities",
]
