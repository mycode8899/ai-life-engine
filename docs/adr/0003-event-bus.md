# ADR 0003: Event bus without an implicit model call

Date: 2026-11-26

Accepted for 0.3.0. Memory shipped in 0.4.0 and is a separate store. See [ADR 0004](0004-memory.md). Remembering does not publish an event.

## Context

Phase 2 can tick on an interval and can be poked with `heartbeat.wake()`. That method is a scheduler signal. It does not carry a payload, it does not have subscribers, and it does not survive as a log of what happened. Phase 3 needs an event bus that can activate a running engine independently of the schedule, including when no heartbeat interval is configured.

The bus must not call a language model because an event arrived.

## Decision

1. **The bus is a real package.** `ai_life_engine.events` owns `subscribe`, `unsubscribe`, `publish`, and `history`. `LifeEngine.events` is the handle. `heartbeat.wake()` stays a signal. The bus calls it with reason `event` only after at least one event was delivered and only when `heartbeat_interval` is set. With no interval, delivery still happens and no heartbeat row is created.

2. **Publish stores, then returns.** `publish` validates the type, source, and payload, inserts a `pending` row, and kicks the loop if this process is inside `run`. Handlers run on that loop, on a worker thread, with `event_handler_timeout` (default 1 second). They are not invoked on the publisher thread.

3. **Another process can publish.** A second `LifeEngine` on the same database writes the row and cannot see the first process's asyncio event. The running loop polls every `event_poll_seconds` (default 0.25). In-process publish does not wait for that poll.

4. **Subscriptions are in memory.** They are not written to SQLite. A restart drops them. Pending rows stay pending until a later `run` has a matching subscriber, including `"*"`. An event with no subscriber is left pending and is not retried in a spin. Once any matching subscriber exists, the event is delivered once. A late subscriber sees `history` and does not receive events that were already delivered or failed.

5. **Failure is per event.** If any handler times out or raises, the row becomes `failed` and `handler_error` stores a clipped message. The runtime stays up. The `events` health check warns. It does not fail the report by itself. Failed events are not retried.

6. **No automatic lifecycle events.** Start, stop, pause, and heartbeat ticks do not publish. Callers publish the events they care about.

7. **Persistence is additive.** Schema version moves to `"3"`. Versions `"1"` and `"2"` upgrade by creating the events table. Version `"99"` still raises `schema_mismatch`. Pending rows are capped at 100. Finished rows keep the newest 100 and pending rows are never deleted by that trim. Payloads are string maps and reject secret-looking keys. Types and sources are 1..64 lowercase names.

8. **A store can boot without event methods.** `watch` returns immediately when the store cannot persist events. `publish` then raises `ConfigurationError`. SQLite implements the methods.

9. **The runtime lock is not held across a handler.** Copy the pending batch and the subscription list, release the lock, run handlers, then mark the row.

## Consequences

An external event can activate the engine without waiting for a heartbeat, and without a heartbeat at all. Applications that want the heartbeat to notice the event set an interval. Applications that only want the handler leave the interval unset. Memory, model adapters, and a plugin SDK are still later phases.

## Not decided here

Per-subscriber cursors, retries, dead-letter queues beyond the `failed` status, persisted handlers, and events emitted by the lifecycle itself.
