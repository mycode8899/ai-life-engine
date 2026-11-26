# SPDX-License-Identifier: Apache-2.0
"""The runtime.

A process can initialize an engine, start it, pause, resume, stop, and read
its health without configuring a language model. ``run`` keeps that process
alive until stop is requested. When ``heartbeat_interval`` is set, the same
wait ticks. The default policy does not call a reasoner.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from ai_life_engine.agents.health import agents_check
from ai_life_engine.agents.service import Agents
from ai_life_engine.brain.health import brain_check
from ai_life_engine.brain.service import Brain
from ai_life_engine.core.config import EngineConfig
from ai_life_engine.core.health import (
    RESERVED_HEALTH_CHECKS,
    CheckResult,
    CheckStatus,
    HealthCheck,
    HealthReport,
    consistency_check,
    lifecycle_check,
    storage_check,
    summarize,
    uptime_seconds,
)
from ai_life_engine.core.lifecycle import Lifecycle
from ai_life_engine.core.recovery import assess
from ai_life_engine.core.state import LifecycleState, RuntimeSnapshot
from ai_life_engine.core.time import utcnow
from ai_life_engine.events.bus import EventBus
from ai_life_engine.events.health import events_check
from ai_life_engine.exceptions import (
    AgentError,
    ConfigurationError,
    LifecycleError,
    PersistenceError,
)
from ai_life_engine.goals.health import goals_check
from ai_life_engine.goals.service import Goals
from ai_life_engine.goals.tasks import GoalTaskSource
from ai_life_engine.heartbeat.health import heartbeat_check
from ai_life_engine.heartbeat.models import HeartbeatObservation
from ai_life_engine.heartbeat.policy import Reasoner, ReasoningPolicy
from ai_life_engine.heartbeat.scheduler import Scheduler
from ai_life_engine.heartbeat.service import Heartbeat
from ai_life_engine.heartbeat.store import supports_heartbeat
from ai_life_engine.heartbeat.tasks import TaskSource
from ai_life_engine.memory.health import memory_check
from ai_life_engine.memory.service import Memory
from ai_life_engine.observability import get_logger
from ai_life_engine.persistence.base import StateStore, ensure_store
from ai_life_engine.persistence.models import (
    AuditEntry,
    Checkpoint,
    EngineRecord,
    NewAudit,
    NewCheckpoint,
)
from ai_life_engine.persistence.sqlite import SqliteStateStore
from ai_life_engine.plugins.context import PluginContext
from ai_life_engine.plugins.health import plugins_check
from ai_life_engine.plugins.service import Plugins
from ai_life_engine.validation import bounded_limit, clean_reason
from ai_life_engine.version import __version__

logger = get_logger("ai_life_engine.runtime")

RUNTIME_CAPABILITIES: dict[str, bool] = {
    "lifecycle": True,
    "state_persistence": True,
    "health_checks": True,
    "audit_log": True,
    "checkpoints": True,
    "crash_recovery": True,
    "heartbeat": True,
    "event_bus": True,
    "memory": True,
    "llm": True,
    "goals": True,
    "planning": True,
    "reflection": True,
    "agent_adapters": True,
    "tool_registry": True,
    "rest_api": True,
    "plugin_sdk": True,
}

StartedCallback = Callable[[RuntimeSnapshot], None]


def runtime_capabilities() -> dict[str, bool]:
    """Return the capabilities implemented by this package version."""

    return dict(RUNTIME_CAPABILITIES)


def open_sqlite_store(config: EngineConfig) -> SqliteStateStore:
    """Open the default SQLite store for ``config``."""

    data_dir = config.resolved_data_dir()
    if data_dir.exists() and not data_dir.is_dir():
        raise ConfigurationError(f"Data directory path is a file: {data_dir}")
    return SqliteStateStore(data_dir / config.database_filename)


def _idle_state(record: EngineRecord | None) -> LifecycleState:
    if record is None:
        return LifecycleState.CREATED
    if record.state is LifecycleState.STOPPED:
        return LifecycleState.STOPPED
    if record.state is LifecycleState.FAILED:
        return LifecycleState.FAILED
    return LifecycleState.CREATED


def _as_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError("reason_timeout must be a number of seconds.")
    number = float(value)
    if number < 0.05 or number > 120:
        raise ConfigurationError("reason_timeout must be between 0.05 and 120 seconds.")
    return number


def _require_hook(value: object, method: str, label: str) -> None:
    if value is None:
        return
    if not callable(getattr(value, method, None)):
        raise ConfigurationError(f"{label} must provide {method}().")


def _as_reason(reason: str) -> str:
    try:
        return clean_reason(reason)
    except ValueError as exc:
        raise LifecycleError(str(exc), code="invalid_argument") from exc


def _as_limit(limit: int) -> int:
    try:
        return bounded_limit(limit)
    except ValueError as exc:
        raise LifecycleError(str(exc), code="invalid_argument") from exc


@dataclass(frozen=True)
class _Change:
    state: LifecycleState
    reason: str
    action: str
    run_id: UUID | None
    pid: int | None
    clean_shutdown: bool
    started_at: datetime | None
    stopped_at: datetime | None
    apply_identity: bool = False
    recovery_increment: int = 0
    pause_increment: int = 0


class _HeartbeatHost:
    """Adapter so the heartbeat loop does not import ``LifeEngine``."""

    def __init__(self, engine: LifeEngine) -> None:
        self._engine = engine

    @property
    def lock(self) -> threading.RLock:
        return self._engine._lock

    @property
    def store(self) -> object:
        return self._engine._store

    def engine_id(self) -> UUID:
        return self._engine._engine_id

    def refresh(self) -> None:
        self._engine._refresh_unlocked()

    def snapshot(self) -> RuntimeSnapshot:
        return self._engine._snapshot_unlocked()

    def state(self) -> LifecycleState:
        return self._engine._lifecycle.state

    def owns(self) -> bool:
        return self._engine._owns_runtime

    def has_identity(self) -> bool:
        return self._engine._record is not None

    def ensure_open(self) -> None:
        self._engine._require_open()

    def model_reason(self, observation: HeartbeatObservation) -> str | None:
        brain = self._engine._brain
        if not brain.configured:
            return None
        state = observation.runtime_state.value
        prompt = f"Heartbeat tick. state={state}. tasks={observation.task_count}."
        return brain.complete(prompt).text


class _EventHost:
    """Adapter so the event bus does not import ``LifeEngine``."""

    def __init__(self, engine: LifeEngine) -> None:
        self._engine = engine

    @property
    def lock(self) -> threading.RLock:
        return self._engine._lock

    @property
    def store(self) -> object:
        return self._engine._store

    def engine_id(self) -> UUID:
        return self._engine._engine_id

    def ensure_open(self) -> None:
        self._engine._require_open()

    def refresh(self) -> None:
        self._engine._refresh_unlocked()

    def has_identity(self) -> bool:
        return self._engine._record is not None

    def wake_heartbeat(self, reason: str) -> None:
        if self._engine._config.heartbeat_interval is None:
            return
        self._engine._heartbeat.wake(reason)


class _MemoryHost:
    """Adapter so memory does not import ``LifeEngine``."""

    def __init__(self, engine: LifeEngine) -> None:
        self._engine = engine

    @property
    def lock(self) -> threading.RLock:
        return self._engine._lock

    @property
    def store(self) -> object:
        return self._engine._store

    def engine_id(self) -> UUID:
        return self._engine._engine_id

    def ensure_open(self) -> None:
        self._engine._require_open()

    def refresh(self) -> None:
        self._engine._refresh_unlocked()

    def has_identity(self) -> bool:
        return self._engine._record is not None


class _BrainHost:
    """Adapter so the model facade does not import ``LifeEngine``."""

    def __init__(self, engine: LifeEngine) -> None:
        self._engine = engine

    @property
    def lock(self) -> threading.RLock:
        return self._engine._lock

    @property
    def store(self) -> object:
        return self._engine._store

    def engine_id(self) -> UUID:
        return self._engine._engine_id

    def ensure_open(self) -> None:
        self._engine._require_open()

    def refresh(self) -> None:
        self._engine._refresh_unlocked()

    def has_identity(self) -> bool:
        return self._engine._record is not None


class _GoalHost:
    """Adapter so goals do not import ``LifeEngine``."""

    def __init__(self, engine: LifeEngine) -> None:
        self._engine = engine

    @property
    def lock(self) -> threading.RLock:
        return self._engine._lock

    @property
    def store(self) -> object:
        return self._engine._store

    def engine_id(self) -> UUID:
        return self._engine._engine_id

    def ensure_open(self) -> None:
        self._engine._require_open()

    def refresh(self) -> None:
        self._engine._refresh_unlocked()

    def has_identity(self) -> bool:
        return self._engine._record is not None


class _AgentHost:
    """Adapter so tools do not import ``LifeEngine``."""

    def __init__(self, engine: LifeEngine) -> None:
        self._engine = engine

    @property
    def lock(self) -> threading.RLock:
        return self._engine._lock

    @property
    def store(self) -> object:
        return self._engine._store

    def engine_id(self) -> UUID:
        return self._engine._engine_id

    def ensure_open(self) -> None:
        self._engine._require_open()

    def refresh(self) -> None:
        self._engine._refresh_unlocked()

    def has_identity(self) -> bool:
        return self._engine._record is not None


class _PluginHost:
    """Adapter so the plugin loader does not import ``LifeEngine``."""

    def __init__(self, engine: LifeEngine) -> None:
        self._engine = engine

    @property
    def lock(self) -> threading.RLock:
        return self._engine._lock

    def ensure_open(self) -> None:
        self._engine._require_open()


class LifeEngine:
    """Persistent runtime handle.

    Constructing an engine opens storage and does not start it. Use
    :meth:`start` or the context manager to claim the runtime. Use
    :meth:`close` after :meth:`stop`. ``close`` closes SQLite when this
    engine opened it. An injected store stays open for its owner.

    The handle is safe to use from the thread that drives it. ``request_stop``
    may be called from another thread.
    """

    def __init__(
        self,
        config: EngineConfig | None = None,
        *,
        store: StateStore | None = None,
        scheduler: Scheduler | None = None,
        reasoning_policy: ReasoningPolicy | None = None,
        reasoner: Reasoner | None = None,
        task_source: TaskSource | None = None,
        reason_timeout: float = 5.0,
    ) -> None:
        if config is None:
            config = EngineConfig()
        if not isinstance(config, EngineConfig):
            raise ConfigurationError("config must be an EngineConfig.")
        timeout = _as_timeout(reason_timeout)
        _require_hook(scheduler, "wait_for_wake", "scheduler")
        _require_hook(reasoning_policy, "decide", "reasoning_policy")
        _require_hook(reasoner, "reason", "reasoner")
        _require_hook(task_source, "inspect", "task_source")
        self._config = config
        self._heartbeat_hooks = (scheduler, reasoning_policy, reasoner, task_source, timeout)
        self._closed = False
        self._owns_runtime = False
        self._owns_store = store is None
        self._in_health = False
        self._lock = threading.RLock()
        self._checks: list[HealthCheck] = []
        self._stop_requested = threading.Event()
        self._stop_reason = "requested"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_future: asyncio.Future[None] | None = None
        self._opened_at = utcnow()
        self._lifecycle = Lifecycle(LifecycleState.CREATED)
        self._record: EngineRecord | None = None
        self._engine_id: UUID = config.engine_id or uuid4()
        if store is None:
            self._store: StateStore = open_sqlite_store(config)
        else:
            ensure_store(store)
            self._store = store
        try:
            self._bind_existing()
            if self._config.heartbeat_interval is not None and not supports_heartbeat(self._store):
                raise ConfigurationError(
                    "heartbeat_interval requires a state store with heartbeat persistence."
                )
            self._memory = Memory(_MemoryHost(self))
            self._brain = Brain(_BrainHost(self), timeout=self._config.model_timeout)
            self._goals = Goals(_GoalHost(self), complete=self._complete_for_plan)
            self._agents = Agents(
                _AgentHost(self),
                note=self._note_from_tool,
                finish=self._finish_from_tool,
                next_action=self._next_action_text,
            )
            self._plugins = Plugins(_PluginHost(self), context=PluginContext(self))
            scheduler, policy, reasoner, task_source, timeout = self._heartbeat_hooks
            if task_source is None:
                task_source = GoalTaskSource(self._goals)
            self._heartbeat = Heartbeat(
                _HeartbeatHost(self),
                interval=self._config.heartbeat_interval,
                max_failures=self._config.heartbeat_max_failures,
                scheduler=scheduler,
                reasoning_policy=policy,
                reasoner=reasoner,
                task_source=task_source,
                reason_timeout=timeout,
            )
            self._events = EventBus(
                _EventHost(self),
                poll_seconds=self._config.event_poll_seconds,
                handler_timeout=self._config.event_handler_timeout,
            )
        except Exception:
            if self._owns_store:
                self._store.close()
            self._closed = True
            raise

    def __enter__(self) -> LifeEngine:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        stop_error: Exception | None = None
        try:
            if self._owns_runtime:
                reason = "context exit" if exc_type is None else "context exception"
                self.stop(reason=reason)
        except Exception as caught:
            stop_error = caught
            logger.exception("Failed to stop the runtime while leaving its context.")
        try:
            if not self._owns_runtime:
                self.close()
        except Exception:
            logger.exception("Failed to close the runtime.")
            if stop_error is None and exc_type is None:
                raise
        if stop_error is not None and exc_type is None:
            raise stop_error
        return False

    def __del__(self) -> None:
        try:
            if not getattr(self, "_owns_store", False) or getattr(self, "_closed", True):
                return
            store = getattr(self, "_store", None)
            if store is not None:
                store.close()
                self._closed = True
        except Exception:
            return

    def __repr__(self) -> str:
        state = self._lifecycle.state.value
        return f"LifeEngine(engine_id={self._engine_id!s}, state={state})"

    @property
    def config(self) -> EngineConfig:
        return self._config

    @property
    def engine_id(self) -> UUID:
        return self._engine_id

    @property
    def state(self) -> LifecycleState:
        return self._lifecycle.state

    @property
    def owns_runtime(self) -> bool:
        return self._owns_runtime

    @property
    def capabilities(self) -> dict[str, bool]:
        return runtime_capabilities()

    @property
    def heartbeat(self) -> Heartbeat:
        """Heartbeat controller.

        Interval ticks run only inside :meth:`run` when
        ``config.heartbeat_interval`` is set. ``wake`` is a signal, not an
        event bus.
        """

        self._require_open()
        return self._heartbeat

    @property
    def events(self) -> EventBus:
        """Event bus. Publishing stores an event and wakes a running engine.

        Handlers run on the ``run`` loop. ``wake`` on the heartbeat remains a
        scheduler signal. This bus is the subscription log.
        """

        self._require_open()
        return self._events

    @property
    def memory(self) -> Memory:
        """Working, episodic, and semantic memory.

        Records survive a restart. Remembering does not call a model and does
        not wake the heartbeat.
        """

        self._require_open()
        return self._memory

    @property
    def brain(self) -> Brain:
        """Model facade. No provider is selected until :meth:`Brain.use`.

        A selected provider is called from a heartbeat tick only when the
        reasoning policy sets ``required=True`` and no reasoner was supplied.
        The default policy does not do that.
        """

        self._require_open()
        return self._brain

    @property
    def goals(self) -> Goals:
        """Goals, plans, and reflections.

        ``plan`` stores steps the caller supplies. ``propose`` is the only
        method that calls a selected model. ``next_action`` returns a pending
        step and does not execute it. The default heartbeat counts those
        steps and still does not call a model.
        """

        self._require_open()
        return self._goals

    @property
    def agents(self) -> Agents:
        """Tool registry and agent adapter.

        Built-in tools are registered and denied until :meth:`Agents.allow`.
        Grants last for this process only. ``step`` asks the selected adapter
        for one tool. The heartbeat does not call it. No tool opens a shell.
        """

        self._require_open()
        return self._agents

    @property
    def plugins(self) -> Plugins:
        """Plugin loader.

        Nothing is loaded until :meth:`Plugins.load` or :meth:`Plugins.install`.
        Setup may register tools, health checks, and event handlers. It cannot
        grant a permission. A failed plugin does not stop the runtime.
        """

        self._require_open()
        return self._plugins

    def _note_from_tool(self, text: str) -> None:
        self._memory.remember_episode(text, source="tool")

    def _finish_from_tool(self, text: str) -> str:
        action = self._goals.next_action()
        if action is None:
            raise AgentError("No next action is waiting.", code="conflict")
        self._goals.reflect(
            action.goal_id,
            outcome="succeeded",
            note=text,
            step_id=action.step_id,
        )
        return action.action

    def _next_action_text(self) -> str | None:
        action = self._goals.next_action()
        if action is None:
            return None
        return action.action

    def _complete_for_plan(self, prompt: str) -> str:
        return self._brain.complete(prompt).text

    def initialize(self) -> RuntimeSnapshot:
        """Write the runtime identity if this data directory does not have one.

        An existing identity is left unchanged. Starting the runtime also
        creates the identity, so calling this method is optional.
        """

        self._guard()
        with self._lock:
            self._refresh_unlocked()
            if self._record is not None:
                return self._snapshot_unlocked()
            now = utcnow()
            record = EngineRecord(
                engine_id=self._engine_id,
                name=self._desired_name(None),
                created_at=now,
                updated_at=now,
                state=LifecycleState.STOPPED,
                clean_shutdown=True,
                generation=1,
                software_version=__version__,
                metadata=self._desired_metadata(None),
            )
            committed = self._store.commit_state(
                record,
                expected_generation=None,
                audit=NewAudit(
                    engine_id=self._engine_id,
                    run_id=None,
                    action="engine.initialize",
                    from_state=self._lifecycle.state,
                    to_state=LifecycleState.STOPPED,
                    detail="initialize",
                    created_at=now,
                ),
                checkpoint=NewCheckpoint(
                    engine_id=self._engine_id,
                    run_id=None,
                    state=LifecycleState.STOPPED,
                    reason="initialize",
                    created_at=now,
                    detail={"previous_state": self._lifecycle.state.value},
                ),
            )
            self._record = committed.record
            self._lifecycle.bind(LifecycleState.STOPPED)
            logger.info(
                "lifecycle transition to=stopped engine_id=%s run_id= reason=initialize",
                self._engine_id,
            )
            return self._snapshot_unlocked()

    def start(self) -> RuntimeSnapshot:
        """Claim the runtime and mark it running.

        If the previous process left an active or failed record and that
        process is gone, this call recovers the runtime and increments
        ``recovery_count``. A live pid is never taken over.
        """

        self._guard()
        with self._lock:
            if self._owns_runtime:
                raise LifecycleError(
                    "Runtime is already active in this process.",
                    code="already_running",
                    state=self._lifecycle.state,
                )
            self._refresh_unlocked()
            if self._lifecycle.state not in {
                LifecycleState.CREATED,
                LifecycleState.STOPPED,
                LifecycleState.FAILED,
            }:
                raise LifecycleError(
                    f"Cannot start from {self._lifecycle.state.value}.",
                    code="invalid_transition",
                    state=self._lifecycle.state,
                )
            assessment = assess(self._record)
            if assessment.conflict:
                raise LifecycleError(
                    f"Runtime is already active under pid {assessment.previous_pid}.",
                    code="foreign_runtime",
                    state=assessment.previous_state,
                )
            recovering = assessment.required or self._lifecycle.state is LifecycleState.FAILED
            claim = LifecycleState.RECOVERING if recovering else LifecycleState.STARTING
            if not self._lifecycle.can(claim):
                raise LifecycleError(
                    f"Cannot move from {self._lifecycle.state.value} to {claim.value}.",
                    code="invalid_transition",
                    state=self._lifecycle.state,
                )
            run_id = uuid4()
            started_at = utcnow()
            reason = "recover" if recovering else "start"
            action = "lifecycle.recover" if recovering else "lifecycle.start"
            try:
                self._persist(
                    _Change(
                        state=claim,
                        reason=reason,
                        action=action,
                        run_id=run_id,
                        pid=os.getpid(),
                        clean_shutdown=False,
                        started_at=started_at,
                        stopped_at=None,
                        apply_identity=True,
                    )
                )
                self._persist(
                    _Change(
                        state=LifecycleState.RUNNING,
                        reason=reason,
                        action="lifecycle.running",
                        run_id=run_id,
                        pid=os.getpid(),
                        clean_shutdown=False,
                        started_at=started_at,
                        stopped_at=None,
                        apply_identity=True,
                        recovery_increment=1 if recovering else 0,
                    )
                )
            except Exception:
                logger.exception("Runtime start failed.")
                self._fail("start failed")
                raise
            self._owns_runtime = True
            return self._snapshot_unlocked()

    def pause(self) -> RuntimeSnapshot:
        """Pause a runtime owned by this process. The run id does not change."""

        self._guard()
        with self._lock:
            self._require_running("Pause")
            record = self._require_record()
            try:
                self._persist(
                    self._carry(record, LifecycleState.PAUSING, "pause", "lifecycle.pause")
                )
                self._persist(
                    self._carry(
                        self._require_record(),
                        LifecycleState.PAUSED,
                        "pause",
                        "lifecycle.paused",
                        pause_increment=1,
                    )
                )
            except Exception:
                logger.exception("Runtime pause failed.")
                self._fail("pause failed")
                raise
            return self._snapshot_unlocked()

    def resume(self) -> RuntimeSnapshot:
        """Resume a runtime that this process has paused."""

        self._guard()
        with self._lock:
            if not self._owns_runtime or self._lifecycle.state is not LifecycleState.PAUSED:
                raise LifecycleError(
                    "Resume is only available while this process has the runtime paused.",
                    code="not_running",
                    state=self._lifecycle.state,
                )
            record = self._require_record()
            try:
                self._persist(
                    self._carry(record, LifecycleState.RESUMING, "resume", "lifecycle.resume")
                )
                self._persist(
                    self._carry(
                        self._require_record(),
                        LifecycleState.RUNNING,
                        "resume",
                        "lifecycle.running",
                    )
                )
            except Exception:
                logger.exception("Runtime resume failed.")
                self._fail("resume failed")
                raise
            return self._snapshot_unlocked()

    def stop(self, *, reason: str = "requested") -> RuntimeSnapshot:
        """Stop a runtime owned by this process.

        Calling stop when this handle does not own the runtime is a no-op and
        does not change another process's claim.
        """

        self._guard()
        with self._lock:
            if not self._owns_runtime:
                return self._snapshot_unlocked()
            clean = _as_reason(reason)
            if self._lifecycle.state not in {
                LifecycleState.RUNNING,
                LifecycleState.PAUSED,
                LifecycleState.FAILED,
            }:
                raise LifecycleError(
                    f"Cannot stop from {self._lifecycle.state.value}.",
                    code="invalid_transition",
                    state=self._lifecycle.state,
                )
            record = self._require_record()
            try:
                self._persist(
                    _Change(
                        state=LifecycleState.STOPPING,
                        reason=clean,
                        action="lifecycle.stop",
                        run_id=record.run_id,
                        pid=os.getpid(),
                        clean_shutdown=False,
                        started_at=record.started_at,
                        stopped_at=None,
                    )
                )
                stopped_at = utcnow()
                current = self._require_record()
                self._persist(
                    _Change(
                        state=LifecycleState.STOPPED,
                        reason=clean,
                        action="lifecycle.stopped",
                        run_id=current.run_id,
                        pid=None,
                        clean_shutdown=True,
                        started_at=current.started_at,
                        stopped_at=stopped_at,
                    )
                )
            except Exception:
                logger.exception("Runtime stop failed.")
                self._fail("stop failed")
                raise
            self._owns_runtime = False
            self._wake_run_loop()
            self._heartbeat.notify_stop()
            self._events.notify_stop()
            return self._snapshot_unlocked()

    def request_stop(self, reason: str = "requested") -> None:
        """Ask :meth:`run` to stop. Safe to call from another thread."""

        self._stop_reason = _as_reason(reason)
        self._stop_requested.set()
        self._wake_run_loop()
        self._heartbeat.notify_stop()
        self._events.notify_stop()

    async def run(
        self,
        *,
        install_signal_handlers: bool = False,
        on_started: StartedCallback | None = None,
    ) -> None:
        """Start, if needed, and wait until stop is requested.

        When ``heartbeat_interval`` is set, this wait ticks. Published events
        are delivered either way, and a delivered event wakes a sleeping
        heartbeat without waiting for the interval. The default reasoning
        policy does not call a reasoner. On POSIX, signal handlers can be
        installed for SIGINT and SIGTERM. On Windows, only Ctrl+C is
        installed, and only from the main thread.
        """

        self._stop_requested.clear()
        self._stop_reason = "requested"
        self._heartbeat.clear_stop()
        self._events.clear_stop()
        installed: list[signal.Signals] = []
        windows_previous: signal.Handlers | None = None
        windows_installed = False
        try:
            if not self._owns_runtime:
                self.start()
            if install_signal_handlers:
                installed, windows_previous, windows_installed = self._install_signals()
            if on_started is not None:
                on_started(self.status())
            if not self._owns_runtime or self._stop_requested.is_set():
                return
            await self._serve()
        finally:
            self._remove_signals(installed, windows_previous, windows_installed)
            if self._owns_runtime:
                self.stop(reason=self._stop_reason)

    def status(self) -> RuntimeSnapshot:
        """Read the handle and refresh its view of durable state."""

        self._require_open()
        with self._lock:
            self._refresh_unlocked()
            return self._snapshot_unlocked()

    def health(self) -> HealthReport:
        """Run built-in checks and any checks registered on this handle."""

        self._require_open()
        with self._lock:
            if self._in_health:
                raise LifecycleError(
                    "Health checks cannot call health().",
                    code="invalid_argument",
                )
            self._in_health = True
            try:
                storage_error: str | None = None
                try:
                    self._refresh_unlocked()
                    self._store.ping()
                    storage_ok = True
                except PersistenceError as exc:
                    storage_ok = False
                    storage_error = str(exc)
                snapshot = self._snapshot_unlocked()
                checks = [
                    storage_check(ok=storage_ok, error=storage_error),
                    lifecycle_check(snapshot),
                    consistency_check(snapshot, storage_ok=storage_ok),
                    heartbeat_check(
                        None if not storage_ok else self._heartbeat.inspect_for_health(),
                        owns_runtime=snapshot.owns_runtime,
                        state=snapshot.state,
                    ),
                    events_check(None if not storage_ok else self._events.latest_for_health()),
                    memory_check(None if not storage_ok else self._memory.counts_for_health()),
                    brain_check(None if not storage_ok else self._brain.latest_for_health()),
                    goals_check(None if not storage_ok else self._goals.summary_for_health()),
                    agents_check(None if not storage_ok else self._agents.latest_for_health()),
                    plugins_check(self._plugins.records()),
                ]
                for check in self._checks:
                    checks.append(self._run_extra(check, snapshot))
                status = summarize(
                    checks,
                    state=snapshot.state,
                    owns_runtime=snapshot.owns_runtime,
                )
                return HealthReport(
                    status=status,
                    engine_id=snapshot.engine_id,
                    name=snapshot.name,
                    state=snapshot.state,
                    owns_runtime=snapshot.owns_runtime,
                    recovery_required=snapshot.recovery_required,
                    blocked_by_live_process=snapshot.blocked_by_live_process,
                    uptime_seconds=uptime_seconds(snapshot),
                    checked_at=utcnow(),
                    software_version=snapshot.software_version,
                    checks=checks,
                )
            finally:
                self._in_health = False

    def register_health_check(self, check: HealthCheck) -> None:
        """Register an extra health check. Names must be unique identifiers."""

        self._guard()
        name = getattr(check, "name", None)
        if not isinstance(name, str) or not name.isidentifier():
            raise ConfigurationError("Health check name must be an identifier.")
        if name in RESERVED_HEALTH_CHECKS:
            raise ConfigurationError(f"Health check name {name!r} is reserved.")
        if not callable(getattr(check, "run", None)):
            raise ConfigurationError("Health check must provide run().")
        with self._lock:
            if any(existing.name == name for existing in self._checks):
                raise ConfigurationError(f"Health check {name!r} is already registered.")
            self._checks.append(check)

    def audit_trail(self, *, limit: int = 100) -> list[AuditEntry]:
        """Return recent audit records, newest first."""

        self._require_open()
        bounded = _as_limit(limit)
        with self._lock:
            return self._store.list_audit(self._engine_id, limit=bounded)

    def checkpoints(self, *, limit: int = 100) -> list[Checkpoint]:
        """Return recent checkpoints, newest first."""

        self._require_open()
        bounded = _as_limit(limit)
        with self._lock:
            return self._store.list_checkpoints(self._engine_id, limit=bounded)

    def close(self) -> None:
        """Release storage. The runtime must be stopped first."""

        with self._lock:
            if self._closed:
                return
            if self._in_health:
                raise LifecycleError(
                    "Cannot close the runtime from a health check.",
                    code="invalid_argument",
                    state=self._lifecycle.state,
                )
            if self._owns_runtime:
                raise LifecycleError(
                    "Stop the runtime before closing it.",
                    code="still_active",
                    state=self._lifecycle.state,
                )
            self._closed = True
            if self._owns_store:
                self._store.close()

    def _bind_existing(self) -> None:
        loaded = self._store.load(self._config.engine_id)
        if loaded is None:
            self._engine_id = self._config.engine_id or self._engine_id
            self._record = None
            self._lifecycle.bind(LifecycleState.CREATED)
            return
        self._engine_id = loaded.engine_id
        self._record = loaded
        self._lifecycle.bind(_idle_state(loaded))

    def _refresh_unlocked(self) -> None:
        loaded = self._store.load(self._engine_id)
        if self._owns_runtime and loaded is None:
            raise PersistenceError("Persisted runtime disappeared.", code="missing")
        self._record = loaded
        if not self._owns_runtime:
            self._lifecycle.bind(_idle_state(loaded))

    def _persist(self, change: _Change) -> None:
        if not self._lifecycle.can(change.state):
            raise LifecycleError(
                f"Cannot move from {self._lifecycle.state.value} to {change.state.value}.",
                code="invalid_transition",
                state=self._lifecycle.state,
            )
        current = self._record
        now = utcnow()
        reason = _as_reason(change.reason)
        if change.apply_identity:
            name = self._desired_name(current)
        else:
            name = self._existing_name(current)
        metadata = (
            self._desired_metadata(current)
            if change.apply_identity
            else self._existing_metadata(current)
        )
        record = EngineRecord(
            engine_id=self._engine_id,
            name=name,
            created_at=current.created_at if current is not None else now,
            updated_at=now,
            state=change.state,
            run_id=change.run_id,
            pid=change.pid,
            started_at=change.started_at,
            stopped_at=change.stopped_at,
            clean_shutdown=change.clean_shutdown,
            recovery_count=(0 if current is None else current.recovery_count)
            + change.recovery_increment,
            pause_count=(0 if current is None else current.pause_count) + change.pause_increment,
            generation=1 if current is None else current.generation,
            software_version=__version__,
            metadata=metadata,
        )
        from_state = self._lifecycle.state
        committed = self._store.commit_state(
            record,
            expected_generation=None if current is None else current.generation,
            audit=NewAudit(
                engine_id=self._engine_id,
                run_id=change.run_id,
                action=change.action,
                from_state=from_state,
                to_state=change.state,
                detail=reason,
                created_at=now,
            ),
            checkpoint=NewCheckpoint(
                engine_id=self._engine_id,
                run_id=change.run_id,
                state=change.state,
                reason=reason,
                created_at=now,
                detail={
                    "previous_state": from_state.value,
                    "pid": "" if change.pid is None else str(change.pid),
                    "clean_shutdown": "true" if change.clean_shutdown else "false",
                },
            ),
        )
        self._record = committed.record
        self._lifecycle.transition(change.state)
        log = logger.warning if change.action == "lifecycle.recover" else logger.info
        log(
            "lifecycle transition to=%s engine_id=%s run_id=%s reason=%s",
            change.state.value,
            self._engine_id,
            "-" if change.run_id is None else change.run_id,
            reason,
        )

    def _fail(self, detail: str) -> None:
        self._owns_runtime = False
        if self._record is None or not self._lifecycle.can(LifecycleState.FAILED):
            return
        current = self._record
        try:
            self._persist(
                _Change(
                    state=LifecycleState.FAILED,
                    reason=detail,
                    action="lifecycle.fail",
                    run_id=current.run_id,
                    pid=None,
                    clean_shutdown=False,
                    started_at=current.started_at,
                    stopped_at=utcnow(),
                )
            )
        except Exception:
            logger.exception("Failed to persist the failure state.")

    def _carry(
        self,
        record: EngineRecord,
        state: LifecycleState,
        reason: str,
        action: str,
        *,
        pause_increment: int = 0,
    ) -> _Change:
        return _Change(
            state=state,
            reason=reason,
            action=action,
            run_id=record.run_id,
            pid=os.getpid(),
            clean_shutdown=False,
            started_at=record.started_at,
            stopped_at=None,
            pause_increment=pause_increment,
        )

    def _snapshot_unlocked(self) -> RuntimeSnapshot:
        record = self._record
        assessment = assess(record)
        blocked = assessment.conflict and not self._owns_runtime
        recovery_required = assessment.required and not self._owns_runtime
        if record is None:
            return RuntimeSnapshot(
                engine_id=self._engine_id,
                name=self._desired_name(None),
                state=self._lifecycle.state,
                persisted_state=None,
                run_id=None,
                pid=None,
                started_at=None,
                updated_at=self._opened_at,
                stopped_at=None,
                clean_shutdown=False,
                recovery_required=False,
                blocked_by_live_process=False,
                recovery_count=0,
                pause_count=0,
                software_version=__version__,
                metadata=self._desired_metadata(None),
                owns_runtime=self._owns_runtime,
                data_dir=self._data_dir(),
            )
        return RuntimeSnapshot(
            engine_id=record.engine_id,
            name=record.name,
            state=self._lifecycle.state,
            persisted_state=record.state,
            run_id=record.run_id,
            pid=record.pid,
            started_at=record.started_at,
            updated_at=record.updated_at,
            stopped_at=record.stopped_at,
            clean_shutdown=record.clean_shutdown,
            recovery_required=recovery_required,
            blocked_by_live_process=blocked,
            recovery_count=record.recovery_count,
            pause_count=record.pause_count,
            software_version=record.software_version,
            metadata=dict(record.metadata),
            owns_runtime=self._owns_runtime,
            data_dir=self._data_dir(),
        )

    def _run_extra(self, check: HealthCheck, snapshot: RuntimeSnapshot) -> CheckResult:
        name = check.name
        try:
            result = check.run(snapshot)
        except Exception:
            logger.exception("Health check %s failed.", name)
            return CheckResult(
                name=name,
                status=CheckStatus.FAIL,
                message="Health check raised an exception.",
            )
        if not isinstance(result, CheckResult):
            return CheckResult(
                name=name,
                status=CheckStatus.FAIL,
                message="Health check returned an invalid result.",
            )
        return result.model_copy(update={"name": name})

    def _desired_name(self, record: EngineRecord | None) -> str:
        if self._config.name is not None:
            return self._config.name
        if record is not None:
            return record.name
        return "ai-life-engine"

    def _desired_metadata(self, record: EngineRecord | None) -> dict[str, str]:
        if self._config.metadata is not None:
            return dict(self._config.metadata)
        if record is not None:
            return dict(record.metadata)
        return {}

    def _existing_name(self, record: EngineRecord | None) -> str:
        if record is None:
            return self._desired_name(None)
        return record.name

    def _existing_metadata(self, record: EngineRecord | None) -> dict[str, str]:
        if record is None:
            return {}
        return dict(record.metadata)

    def _data_dir(self) -> Path:
        try:
            return self._config.resolved_data_dir()
        except ConfigurationError:
            return self._config.data_dir

    def _require_record(self) -> EngineRecord:
        if self._record is None:
            raise PersistenceError("Runtime identity has not been created.", code="missing")
        return self._record

    def _require_running(self, verb: str) -> None:
        if not self._owns_runtime or self._lifecycle.state is not LifecycleState.RUNNING:
            raise LifecycleError(
                f"{verb} is only available while this process is running the runtime.",
                code="not_running",
                state=self._lifecycle.state,
            )

    def _require_open(self) -> None:
        if self._closed:
            raise LifecycleError("This runtime handle is closed.", code="closed")

    def _guard(self) -> None:
        self._require_open()
        if self._in_health:
            raise LifecycleError(
                "Lifecycle methods cannot be called from a health check.",
                code="invalid_argument",
                state=self._lifecycle.state,
            )

    async def _serve(self) -> None:
        watcher = asyncio.create_task(self._events.watch())
        try:
            if self._config.heartbeat_interval is None:
                await self._wait_until_stop_requested()
            else:
                await self._heartbeat.run_until_stop()
        finally:
            self._events.notify_stop()
            if not watcher.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(watcher),
                        timeout=self._config.event_handler_timeout + 1,
                    )
                except TimeoutError:
                    watcher.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await watcher

    async def _wait_until_stop_requested(self) -> None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        self._loop = loop
        self._stop_future = future
        try:
            if self._stop_requested.is_set() and not future.done():
                future.set_result(None)
            await future
        finally:
            self._loop = None
            self._stop_future = None

    def _wake_run_loop(self) -> None:
        loop = self._loop
        future = self._stop_future
        if loop is None or future is None:
            return

        def _finish() -> None:
            if not future.done():
                future.set_result(None)

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _finish()
            return
        try:
            loop.call_soon_threadsafe(_finish)
        except RuntimeError:
            return

    def _install_signals(
        self,
    ) -> tuple[list[signal.Signals], signal.Handlers | None, bool]:
        if sys.platform == "win32":
            previous = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, lambda _signum, _frame: self.request_stop("signal"))
            return [], previous, True
        loop = asyncio.get_running_loop()
        installed: list[signal.Signals] = []
        for current in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(current, self.request_stop, "signal")
            installed.append(current)
        return installed, None, False

    def _remove_signals(
        self,
        installed: list[signal.Signals],
        windows_previous: signal.Handlers | None,
        windows_installed: bool,
    ) -> None:
        if windows_installed and windows_previous is not None:
            signal.signal(signal.SIGINT, windows_previous)
        if not installed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for current in installed:
            try:
                loop.remove_signal_handler(current)
            except (RuntimeError, ValueError):
                logger.warning("Could not remove a runtime signal handler.")
                return
