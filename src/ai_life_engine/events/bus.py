# SPDX-License-Identifier: Apache-2.0
"""In-process event bus.

Publishing stores an event and, when this process is running, delivers it
to subscribers. Delivery can wake a sleeping heartbeat. It does not call a
language model. Subscriptions live in memory; the log is what survives a
restart.
"""

from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from ai_life_engine.core.time import utcnow
from ai_life_engine.events.models import MAX_PENDING_EVENTS, EngineEvent, EventStatus
from ai_life_engine.events.store import supports_events
from ai_life_engine.exceptions import ConfigurationError, LifecycleError, PersistenceError
from ai_life_engine.observability import get_logger
from ai_life_engine.validation import bounded_limit, clean_metadata, clean_reason

logger = get_logger("ai_life_engine.events")

_NAME = re.compile(r"[a-z][a-z0-9._-]{0,63}")
_MAX_SUBSCRIPTIONS = 100
EventHandler = Callable[[EngineEvent], None]


class EventHost(Protocol):
    """Narrow view of the runtime the bus is allowed to touch."""

    @property
    def lock(self) -> threading.RLock: ...

    @property
    def store(self) -> object: ...

    def engine_id(self) -> UUID: ...

    def ensure_open(self) -> None: ...

    def refresh(self) -> None: ...

    def has_identity(self) -> bool: ...

    def wake_heartbeat(self, reason: str) -> None: ...


@dataclass
class _Subscription:
    id: str
    event_type: str
    handler: EventHandler


def _event_name(value: str, *, label: str) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise LifecycleError(
            f"{label} must be 1..64 lowercase characters, digits, or . _ -.",
            code="invalid_argument",
        )
    return value


def _clip(value: object, fallback: str) -> str:
    text = value if isinstance(value, str) else fallback
    try:
        return clean_reason(text)
    except ValueError:
        return clean_reason(fallback)


class EventBus:
    """Publish, subscribe, and deliver events for one runtime handle."""

    def __init__(self, host: EventHost, *, poll_seconds: float, handler_timeout: float) -> None:
        self._host = host
        self._poll = poll_seconds
        self._handler_timeout = handler_timeout
        self._subscriptions: list[_Subscription] = []
        self._stop_flag = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._kick: asyncio.Event | None = None

    def subscribe(self, event_type: str, handler: EventHandler) -> str:
        """Register a handler. ``event_type`` may be ``*`` to receive every event."""

        self._host.ensure_open()
        if event_type != "*":
            _event_name(event_type, label="Event type")
        if not callable(handler):
            raise ConfigurationError("An event handler must be callable.")
        with self._host.lock:
            if len(self._subscriptions) >= _MAX_SUBSCRIPTIONS:
                raise LifecycleError(
                    "Too many event subscriptions.",
                    code="invalid_argument",
                )
            subscription = _Subscription(id=uuid4().hex, event_type=event_type, handler=handler)
            self._subscriptions.append(subscription)
            return subscription.id

    def unsubscribe(self, subscription_id: str) -> None:
        """Remove one subscription. Unknown ids raise."""

        self._host.ensure_open()
        with self._host.lock:
            kept = [item for item in self._subscriptions if item.id != subscription_id]
            if len(kept) == len(self._subscriptions):
                raise LifecycleError(
                    "That event subscription does not exist.",
                    code="invalid_argument",
                )
            self._subscriptions = kept

    def publish(
        self,
        event_type: str,
        *,
        source: str = "external",
        payload: dict[str, str] | None = None,
    ) -> EngineEvent:
        """Store an event and ask a running engine to deliver it.

        Safe to call from another thread. Returns after the row is stored.
        Handlers run on the running engine's event loop, not on this call.
        """

        self._host.ensure_open()
        cleaned_type = _event_name(event_type, label="Event type")
        cleaned_source = _event_name(source, label="Event source")
        try:
            cleaned_payload = clean_metadata({} if payload is None else payload)
        except ValueError as exc:
            raise LifecycleError(str(exc), code="invalid_argument") from exc
        with self._host.lock:
            self._host.refresh()
            if not self._host.has_identity():
                raise LifecycleError(
                    "Initialize or start the runtime before publishing events.",
                    code="not_running",
                )
            store = self._host.store
            if not supports_events(store):
                raise ConfigurationError("This state store does not persist events.")
            if store.count_pending_events(self._host.engine_id()) >= MAX_PENDING_EVENTS:
                raise LifecycleError("Too many pending events.", code="invalid_argument")
            event = store.append_event(
                event_id=uuid4(),
                engine_id=self._host.engine_id(),
                event_type=cleaned_type,
                source=cleaned_source,
                payload=cleaned_payload,
                created_at=utcnow(),
            )
        self._kick_loop()
        logger.info("event published type=%s id=%s", cleaned_type, event.event_id)
        return event

    def history(self, *, limit: int = 20) -> list[EngineEvent]:
        """Return recent events, newest first."""

        self._host.ensure_open()
        bounded = _as_limit(limit)
        store = self._host.store
        if not supports_events(store):
            return []
        with self._host.lock:
            return store.list_events(self._host.engine_id(), limit=bounded)

    def latest_for_health(self) -> EngineEvent | None:
        """Read the newest event for a health report. Storage errors become ``None``."""

        try:
            self._host.ensure_open()
            store = self._host.store
            if not supports_events(store):
                return None
            with self._host.lock:
                return store.latest_event(self._host.engine_id())
        except PersistenceError:
            return None

    def clear_stop(self) -> None:
        """Forget a stop requested before ``run`` starts."""

        self._stop_flag.clear()

    def notify_stop(self) -> None:
        """Unblock the delivery loop. Safe to call from another thread."""

        self._stop_flag.set()
        self._kick_loop()

    async def watch(self) -> None:
        """Deliver pending events until stop. Called from :meth:`LifeEngine.run`."""

        if not supports_events(self._host.store):
            return
        self._loop = asyncio.get_running_loop()
        self._kick = asyncio.Event()
        if self._stop_flag.is_set():
            self._kick.set()
        try:
            while not self._stop_flag.is_set():
                delivered = await self._dispatch()
                if delivered:
                    self._host.wake_heartbeat("event")
                if self._stop_flag.is_set():
                    break
                try:
                    await asyncio.wait_for(self._kick.wait(), timeout=self._poll)
                except TimeoutError:
                    pass
                self._kick.clear()
        finally:
            self._kick = None
            self._loop = None

    async def _dispatch(self) -> int:
        delivered = 0
        while not self._stop_flag.is_set():
            with self._host.lock:
                store = self._host.store
                if not supports_events(store):
                    return delivered
                pending = store.list_pending_events(self._host.engine_id(), limit=20)
                subscriptions = list(self._subscriptions)
            if not pending:
                return delivered
            progressed = False
            for event in pending:
                if self._stop_flag.is_set():
                    return delivered
                matched = [
                    item
                    for item in subscriptions
                    if item.event_type == event.type or item.event_type == "*"
                ]
                if not matched:
                    continue
                errors: list[str] = []
                for item in matched:
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(item.handler, event),
                            timeout=self._handler_timeout,
                        )
                    except TimeoutError:
                        errors.append("Handler timed out.")
                    except Exception as exc:
                        errors.append(_clip(str(exc), "Handler failed."))
                status = EventStatus.FAILED if errors else EventStatus.DELIVERED
                detail = _clip("; ".join(errors), "Handler failed.") if errors else None
                with self._host.lock:
                    self._host.store.mark_event(
                        self._host.engine_id(),
                        event.event_id,
                        status=status,
                        handler_error=detail,
                        delivered_at=utcnow(),
                    )
                delivered += 1
                progressed = True
                if errors:
                    logger.warning("event handler failed type=%s error=%s", event.type, detail)
            if not progressed:
                return delivered
        return delivered

    def _kick_loop(self) -> None:
        loop = self._loop
        event = self._kick
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
