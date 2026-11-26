# SPDX-License-Identifier: Apache-2.0
"""Heartbeat loop.

The loop wakes, inspects state and tasks, asks a policy whether reasoning
is required, and persists the result. The default policy never requests
reasoning. A reasoner is not an LLM client.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from ai_life_engine.core.state import LifecycleState, RuntimeSnapshot
from ai_life_engine.core.time import utcnow
from ai_life_engine.exceptions import ConfigurationError, LifecycleError, PersistenceError
from ai_life_engine.heartbeat.models import (
    HeartbeatObservation,
    HeartbeatSnapshot,
    HeartbeatStatus,
    HeartbeatTick,
    NewHeartbeatTick,
    WakeReason,
)
from ai_life_engine.heartbeat.policy import (
    NeverReasonPolicy,
    Reasoner,
    ReasoningDecision,
    ReasoningPolicy,
)
from ai_life_engine.heartbeat.scheduler import IntervalScheduler, Scheduler
from ai_life_engine.heartbeat.store import supports_heartbeat
from ai_life_engine.heartbeat.tasks import EmptyTaskSource, TaskSource
from ai_life_engine.observability import get_logger
from ai_life_engine.validation import bounded_limit, clean_reason

logger = get_logger("ai_life_engine.heartbeat")

_SPIN_GUARD_SECONDS = 0.01


class HeartbeatHost(Protocol):
    """Narrow view of the runtime the loop is allowed to touch."""

    @property
    def lock(self) -> threading.RLock: ...

    @property
    def store(self) -> object: ...

    def engine_id(self) -> UUID: ...

    def refresh(self) -> None: ...

    def snapshot(self) -> RuntimeSnapshot: ...

    def state(self) -> LifecycleState: ...

    def owns(self) -> bool: ...

    def has_identity(self) -> bool: ...

    def ensure_open(self) -> None: ...


@dataclass(frozen=True)
class _Plan:
    observation: HeartbeatObservation
    decision: ReasoningDecision


def _clip(value: object, fallback: str) -> str:
    text = value if isinstance(value, str) else fallback
    try:
        return clean_reason(text)
    except ValueError:
        return clean_reason(fallback)


def _as_reason(reason: str) -> str:
    try:
        return clean_reason(reason)
    except ValueError as exc:
        raise LifecycleError(str(exc), code="invalid_argument") from exc


def _coerce_wake(value: object) -> WakeReason:
    if isinstance(value, WakeReason):
        return value
    if isinstance(value, str):
        try:
            return WakeReason(value)
        except ValueError:
            pass
    raise RuntimeError("Scheduler returned an unknown wake reason.")


class Heartbeat:
    """Periodic wake-up attached to one runtime handle.

    ``sleep`` suspends interval ticks until ``wake``. Neither method is a
    lifecycle pause. ``wake`` is a signal, not an event bus.
    """

    def __init__(
        self,
        host: HeartbeatHost,
        *,
        interval: float | None,
        max_failures: int,
        scheduler: Scheduler | None,
        reasoning_policy: ReasoningPolicy | None,
        reasoner: Reasoner | None,
        task_source: TaskSource | None,
        reason_timeout: float,
    ) -> None:
        self._host = host
        self._interval = interval
        self._max_failures = max_failures
        self._scheduler: Scheduler = scheduler if scheduler is not None else IntervalScheduler()
        self._policy: ReasoningPolicy = (
            reasoning_policy if reasoning_policy is not None else NeverReasonPolicy()
        )
        self._reasoner = reasoner
        self._tasks: TaskSource = task_source if task_source is not None else EmptyTaskSource()
        self._reason_timeout = reason_timeout
        self._stop_flag = threading.Event()
        self._wake_flag = threading.Event()
        self._pending_reason = "event"
        self._wake_gen = 0
        self._seen_wake_gen = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake_event: asyncio.Event | None = None
        self._stop_event: asyncio.Event | None = None
        self._running = False
        self._generation: int | None = None
        self._current: HeartbeatSnapshot | None = None
        self._reset_memory()

    def status(self) -> HeartbeatSnapshot:
        """Return the latest heartbeat view."""

        self._host.ensure_open()
        with self._host.lock:
            if self._running and self._current is not None:
                return self._current
            loaded = self._load()
            if loaded is not None:
                return loaded
            return self._synthetic()

    def ticks(self, *, limit: int = 20) -> list[HeartbeatTick]:
        """Return recent ticks, newest first."""

        self._host.ensure_open()
        bounded = _as_limit(limit)
        store = self._host.store
        if not supports_heartbeat(store):
            return []
        with self._host.lock:
            return store.list_heartbeat_ticks(self._host.engine_id(), limit=bounded)

    def sleep(self) -> HeartbeatSnapshot:
        """Suspend interval ticks until :meth:`wake`."""

        self._host.ensure_open()
        with self._host.lock:
            self._host.refresh()
            self._ensure_loaded()
            self._prefer_asleep = True
            self._asleep = True
            self._seen_wake_gen = self._wake_gen
            self._wake_flag.clear()
            self._clear_wake_event()
            if self._host.has_identity() and not self._hard_failed:
                self._status = HeartbeatStatus.ASLEEP
                self._commit(tick=None)
            return self._view_unlocked()

    def wake(self, reason: str = "event") -> None:
        """Request one tick and resume interval ticks after it.

        Safe to call from another thread. Repeated wakes coalesce. A persisted
        wake is still pending after a restart.
        """

        self._host.ensure_open()
        cleaned = _as_reason(reason)
        with self._host.lock:
            self._host.refresh()
            self._ensure_loaded()
            self._pending_reason = cleaned
            self._prefer_asleep = False
            self._asleep = False
            self._wake_gen += 1
            self._wake_flag.set()
            self._signal(self._wake_event)
            if not self._host.has_identity():
                return
            if self._running and not self._hard_failed:
                self._status = HeartbeatStatus.BEATING
            self._commit(tick=None)

    def _ensure_loaded(self) -> None:
        """Copy the stored row into memory once, so a later write updates it."""

        if self._generation is not None or not self._host.has_identity():
            return
        existing = self._load()
        if existing is not None:
            self._adopt(existing)

    def clear_stop(self) -> None:
        """Forget a stop requested before ``run`` starts. Matches ``LifeEngine.run``."""

        self._stop_flag.clear()

    def notify_stop(self) -> None:
        """Unblock the loop. Safe to call from another thread."""

        self._stop_flag.set()
        self._signal(self._stop_event)

    def inspect_for_health(self) -> HeartbeatSnapshot | None:
        """Read heartbeat state for a health report. Storage errors become ``None``."""

        try:
            return self.status()
        except PersistenceError:
            return None

    async def run_until_stop(self) -> None:
        """Tick until stop is requested. The caller still owns lifecycle shutdown."""

        if self._interval is None:
            raise ConfigurationError("Heartbeat interval is not configured.")
        self._host.ensure_open()
        self._loop = asyncio.get_running_loop()
        self._wake_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        if self._stop_flag.is_set():
            self._stop_event.set()
        if self._wake_gen != self._seen_wake_gen:
            self._wake_event.set()
        self._running = True
        try:
            self._prepare()
            while not self._stop_flag.is_set():
                interval = None if self._asleep or self._hard_failed else self._interval
                try:
                    reason = _coerce_wake(
                        await self._scheduler.wait_for_wake(
                            interval_seconds=interval,
                            wake=self._wake_event,
                            stop=self._stop_event,
                        )
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._record_failure(f"Scheduler failed: {exc}")
                    await asyncio.sleep(self._interval)
                    continue
                if self._stop_flag.is_set() or reason is WakeReason.STOP:
                    break
                if reason is WakeReason.EVENT:
                    self._consume_wake()
                    self._hard_failed = False
                    self._consecutive = 0
                    self._asleep = False
                    self._prefer_asleep = False
                elif self._asleep or self._hard_failed:
                    if interval is None and reason is WakeReason.INTERVAL:
                        await asyncio.sleep(_SPIN_GUARD_SECONDS)
                    continue
                await self._tick(reason)
        finally:
            self._running = False
            try:
                self._finish()
            except Exception:
                logger.exception("Could not persist the final heartbeat state.")
            self._wake_event = None
            self._stop_event = None
            self._loop = None

    def _prepare(self) -> None:
        with self._host.lock:
            self._host.refresh()
            if not self._host.has_identity():
                raise LifecycleError(
                    "Start the runtime before the heartbeat loop.",
                    code="not_running",
                )
            existing = self._load()
            prefer_asleep = self._prefer_asleep
            wake_gen = self._wake_gen
            seen_wake = self._seen_wake_gen
            pending = self._pending_reason
            self._reset_memory()
            self._wake_gen = wake_gen
            self._seen_wake_gen = seen_wake
            self._pending_reason = pending
            if existing is not None:
                self._adopt(existing)
            if self._wake_gen != self._seen_wake_gen:
                self._asleep = False
                self._prefer_asleep = False
            elif prefer_asleep:
                self._asleep = True
                self._prefer_asleep = True
            self._recovered = bool(existing is not None and existing.in_progress)
            if self._recovered:
                self._last_error = "Recovered an in-progress heartbeat."
            self._in_progress = True
            if self._hard_failed:
                self._status = HeartbeatStatus.FAILED
            elif self._asleep:
                self._status = HeartbeatStatus.ASLEEP
            else:
                self._status = HeartbeatStatus.BEATING
            if self._wake_gen != self._seen_wake_gen and self._wake_event is not None:
                self._wake_event.set()
            self._commit(tick=None)
        if self._recovered:
            logger.warning("Recovered an in-progress heartbeat.")

    def _finish(self) -> None:
        with self._host.lock:
            if self._generation is None:
                return
            self._in_progress = False
            if self._hard_failed:
                self._status = HeartbeatStatus.FAILED
            elif self._asleep or self._prefer_asleep:
                self._asleep = True
                self._status = HeartbeatStatus.ASLEEP
            else:
                self._asleep = False
                self._status = HeartbeatStatus.IDLE
            self._commit(tick=None)

    async def _tick(self, reason: WakeReason) -> None:
        try:
            plan = self._plan(reason)
        except Exception as exc:
            self._record_failure(str(exc))
            return
        if plan is None:
            return
        executed = False
        skipped = False
        detail = plan.decision.reason
        if plan.decision.required:
            if self._reasoner is None:
                model_reason = getattr(self._host, "model_reason", None)
                if model_reason is None:
                    skipped = True
                    detail = "Reasoning was requested but no reasoner is configured."
                else:
                    try:
                        produced = await asyncio.wait_for(
                            asyncio.to_thread(model_reason, plan.observation),
                            timeout=self._reason_timeout,
                        )
                    except TimeoutError:
                        self._record_failure("Reasoner timed out.")
                        return
                    except Exception as exc:
                        self._record_failure(f"Reasoner failed: {exc}")
                        return
                    if produced is None:
                        skipped = True
                        detail = "Reasoning was requested but no reasoner is configured."
                    else:
                        executed = True
                        detail = produced if isinstance(produced, str) else plan.decision.reason
            else:
                try:
                    produced = await asyncio.wait_for(
                        asyncio.to_thread(self._reasoner.reason, plan.observation),
                        timeout=self._reason_timeout,
                    )
                except TimeoutError:
                    self._record_failure("Reasoner timed out.")
                    return
                except Exception as exc:
                    self._record_failure(f"Reasoner failed: {exc}")
                    return
                executed = True
                detail = produced if isinstance(produced, str) else plan.decision.reason
        try:
            self._record_success(plan, executed=executed, skipped=skipped, detail=detail)
        except Exception as exc:
            self._record_failure(str(exc))

    def _plan(self, reason: WakeReason) -> _Plan | None:
        with self._host.lock:
            if not self._host.owns():
                return None
            self._host.refresh()
            state = self._host.state()
            if state is LifecycleState.PAUSED:
                self._skipped += 1
                self._last_detail = "Skipped because the runtime is paused."
                self._last_wake_reason = reason.value
                self._commit(tick=None)
                return None
            if state is not LifecycleState.RUNNING:
                return None
            snapshot = self._host.snapshot()
            report = self._tasks.inspect(snapshot)
            if not isinstance(report.count, int) or isinstance(report.count, bool):
                raise RuntimeError("Task source returned a non-integer count.")
            detail = self._pending_reason if reason is WakeReason.EVENT else reason.value
            observation = HeartbeatObservation(
                engine_id=snapshot.engine_id,
                runtime_state=snapshot.state,
                tick_count=self._tick_count,
                task_count=report.count,
                task_summary=report.summary,
                wake_reason=reason,
                wake_detail=_clip(detail, reason.value),
                asleep=self._asleep,
            )
            decision = self._policy.decide(observation)
            if not isinstance(decision, ReasoningDecision):
                raise RuntimeError("Reasoning policy must return ReasoningDecision.")
            return _Plan(observation=observation, decision=decision)

    def _record_success(
        self,
        plan: _Plan,
        *,
        executed: bool,
        skipped: bool,
        detail: str,
    ) -> None:
        with self._host.lock:
            if not self._host.owns() or self._host.state() is not LifecycleState.RUNNING:
                return
            now = utcnow()
            self._tick_count += 1
            self._consecutive = 0
            self._last_error = None
            self._last_tick_at = now
            self._last_wake_reason = plan.observation.wake_detail
            self._last_detail = _clip(detail, "Heartbeat tick completed.")
            if executed:
                self._reasoning_count += 1
            if skipped:
                self._reasoning_skipped += 1
            self._status = HeartbeatStatus.ASLEEP if self._asleep else HeartbeatStatus.BEATING
            tick_count = self._tick_count
            tick = self._tick_row(
                tick_number=tick_count,
                observation=plan.observation,
                required=plan.decision.required,
                executed=executed,
                detail=self._last_detail,
                created_at=now,
            )
            self._commit(tick=tick)
        logger.info(
            "heartbeat tick=%s reason=%s reasoning=%s",
            tick_count,
            plan.observation.wake_detail,
            executed,
        )

    def _record_failure(self, message: str) -> None:
        with self._host.lock:
            self._failure_count += 1
            self._consecutive += 1
            self._last_error = _clip(message, "Heartbeat tick failed.")
            if self._consecutive >= self._max_failures:
                self._hard_failed = True
                self._status = HeartbeatStatus.FAILED
            elif not self._asleep and self._status is not HeartbeatStatus.FAILED:
                self._status = HeartbeatStatus.BEATING
            try:
                self._commit(tick=None)
            except Exception:
                logger.exception("Could not persist a heartbeat failure.")
        logger.warning(
            "heartbeat tick failed error=%s consecutive=%s",
            self._last_error,
            self._consecutive,
        )

    def _commit(self, *, tick: NewHeartbeatTick | None) -> None:
        store = self._host.store
        if not supports_heartbeat(store):
            raise ConfigurationError(
                "heartbeat_interval requires a state store with heartbeat persistence."
            )
        record = self._snapshot_for_store()
        stored = store.commit_heartbeat(
            record,
            expected_generation=self._generation,
            tick=tick,
        )
        self._generation = stored.generation
        self._current = stored

    def _load(self) -> HeartbeatSnapshot | None:
        store = self._host.store
        if not supports_heartbeat(store):
            return None
        return store.load_heartbeat(self._host.engine_id())

    def _adopt(self, existing: HeartbeatSnapshot) -> None:
        self._generation = existing.generation
        self._tick_count = existing.tick_count
        self._skipped = existing.skipped_count
        self._failure_count = existing.failure_count
        self._consecutive = existing.consecutive_failures
        self._reasoning_count = existing.reasoning_count
        self._reasoning_skipped = existing.reasoning_skipped_count
        self._last_tick_at = existing.last_tick_at
        self._last_wake_reason = existing.last_wake_reason
        self._last_error = existing.last_error
        self._last_detail = existing.last_detail
        self._asleep = existing.asleep or existing.status is HeartbeatStatus.ASLEEP
        self._hard_failed = existing.status is HeartbeatStatus.FAILED
        self._prefer_asleep = self._asleep
        if existing.wake_pending and self._wake_gen == self._seen_wake_gen:
            self._wake_gen += 1
            self._wake_flag.set()

    def _reset_memory(self) -> None:
        self._status = HeartbeatStatus.IDLE
        self._tick_count = 0
        self._skipped = 0
        self._failure_count = 0
        self._consecutive = 0
        self._reasoning_count = 0
        self._reasoning_skipped = 0
        self._last_tick_at = None
        self._last_wake_reason = None
        self._last_error = None
        self._last_detail = None
        self._asleep = False
        self._prefer_asleep = False
        self._recovered = False
        self._hard_failed = False
        self._in_progress = False
        self._generation = None

    def _snapshot_for_store(self) -> HeartbeatSnapshot:
        return HeartbeatSnapshot(
            engine_id=self._host.engine_id(),
            status=self._status,
            interval_seconds=self._interval or 0.0,
            tick_count=self._tick_count,
            skipped_count=self._skipped,
            failure_count=self._failure_count,
            consecutive_failures=self._consecutive,
            reasoning_count=self._reasoning_count,
            reasoning_skipped_count=self._reasoning_skipped,
            last_tick_at=self._last_tick_at,
            last_wake_reason=self._last_wake_reason,
            last_error=self._last_error,
            last_detail=self._last_detail,
            asleep=self._asleep,
            recovered=self._recovered,
            wake_pending=self._wake_gen != self._seen_wake_gen,
            in_progress=self._in_progress,
            generation=0,
            updated_at=utcnow(),
        )

    def _synthetic(self) -> HeartbeatSnapshot:
        return HeartbeatSnapshot(
            engine_id=self._host.engine_id(),
            status=HeartbeatStatus.IDLE,
            interval_seconds=self._interval or 0.0,
            updated_at=utcnow(),
        )

    def _view_unlocked(self) -> HeartbeatSnapshot:
        if self._current is not None:
            return self._current
        loaded = self._load()
        if loaded is not None:
            return loaded
        return self._synthetic()

    def _tick_row(
        self,
        *,
        tick_number: int,
        observation: HeartbeatObservation,
        required: bool,
        executed: bool,
        detail: str,
        created_at: datetime,
    ) -> NewHeartbeatTick:
        return NewHeartbeatTick(
            engine_id=observation.engine_id,
            tick_number=tick_number,
            wake_reason=observation.wake_detail,
            reasoning_required=required,
            reasoning_executed=executed,
            task_count=observation.task_count,
            runtime_state=observation.runtime_state.value,
            detail=detail,
            created_at=created_at,
        )

    def _consume_wake(self) -> None:
        self._seen_wake_gen = self._wake_gen
        self._wake_flag.clear()
        self._clear_wake_event()
        if self._wake_gen != self._seen_wake_gen:
            self._wake_flag.set()

    def _clear_wake_event(self) -> None:
        event = self._wake_event
        if event is not None and event.is_set():
            event.clear()

    def _signal(self, event: asyncio.Event | None) -> None:
        loop = self._loop
        if loop is None or event is None:
            return

        def _set() -> None:
            event.set()

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _set()
            return
        try:
            loop.call_soon_threadsafe(_set)
        except RuntimeError:
            return


def _as_limit(limit: int) -> int:
    try:
        return bounded_limit(limit)
    except ValueError as exc:
        raise LifecycleError(str(exc), code="invalid_argument") from exc
