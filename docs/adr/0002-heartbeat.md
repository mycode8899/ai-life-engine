# ADR 0002: Heartbeat without an implicit model call

Date: 2026-11-26

Accepted for 0.2.0. The event bus this record left unimplemented shipped in 0.3.0. See [ADR 0003](0003-event-bus.md). `heartbeat.wake()` is still only a scheduler signal. The bus calls it after a delivery when an interval is set.

## Context

Phase 1 left `run()` as a wait until stop. The runtime needs a periodic wake-up that can also be poked from outside, that survives a restart, and that does not call a language model just because a timer fired. Phase 3 is an event bus. This phase must not invent that bus in order to wake the loop.

## Decision

1. **Ticking is opt-in.** `EngineConfig.heartbeat_interval = None` keeps the Phase 1 wait. Setting it starts the heartbeat inside `run()`. `start()` and `stop()` do not tick. The context manager does not tick.

2. **The scheduler is a small protocol.** `wait_for_wake(interval_seconds, wake, stop) -> WakeReason`. `None` means wait until wake or stop. Stop wins if both events are set. `IntervalScheduler` is the default. Tests inject a scripted scheduler so the suite does not depend on the wall clock except for one real interval check.

3. **Sleep is not pause.** `heartbeat.sleep()` suspends interval ticks and is persisted. `heartbeat.wake(reason)` requests one tick and resumes the interval. Lifecycle pause also skips ticks, and it is a separate state. `wake` stores a pending flag so a restart still sees it. It is not an event bus, and `ai_life_engine.events` does not exist.

4. **Reasoning is a policy decision.** `NeverReasonPolicy` always returns `required=False`. A `Reasoner` is called only when the policy asks, only if one was supplied, and only under `reason_timeout` (default 5 seconds). If the policy asks and no reasoner exists, the tick succeeds and `reasoning_skipped_count` increases. The core still does not import a provider SDK.

5. **Tasks are an inspection, not a plan.** `EmptyTaskSource` reports zero tasks. A custom source must be fast. There is no planner in this version.

6. **Failures stay inside the heartbeat.** A tick exception increments `failure_count` and the loop continues. After `heartbeat_max_failures` consecutive failures (default 5), status becomes `failed`, interval ticks stop, and `run()` keeps the process alive until stop or `wake`. A successful tick clears the consecutive count.

7. **Persistence is a separate contract.** `StateStore` is unchanged, so a Phase 1 store still boots. `HeartbeatStore` adds `load_heartbeat`, `commit_heartbeat`, and `list_heartbeat_ticks`. SQLite implements it. Schema version moves from `"1"` to `"2"` by creating the new tables. Version `"99"` and anything else still raise `schema_mismatch`. Tick history keeps 50 rows. A tick does not write a lifecycle checkpoint.

8. **Crash recovery uses `in_progress`.** The loop sets that flag while `run()` is inside the heartbeat and clears it on the way out. The next start that sees the flag sets `recovered` and continues. A clean stop leaves the flag clear. An intentional sleep stays asleep.

9. **Health stays quiet when the loop is off.** The built-in `heartbeat` check passes for idle, asleep, and a fresh beat. It warns when a beat is stale or a previous run failed, and it fails only while this process is running a heartbeat that has given up. The name is reserved.

10. **The lock is not held across an await.** State inspection and the policy run under the runtime lock. The reasoner and the scheduler wait do not. `wake` and `request_stop` are safe from another thread.

11. **Bounds.** Interval 0.01..86400 seconds. Reason timeout 0.05..120 seconds. A scheduler that returns an interval wake when it was asked to sleep is delayed by a short guard so a buggy scheduler cannot spin the process.

## Consequences

Applications that want a silent long-running process leave the interval unset. Applications that want ticks set the interval and still get `reasoning_count == 0` until they install both a policy and a reasoner. Phase 3 can deliver real events into `wake` later without changing this loop's contract. Custom stores that enable the interval must implement the three heartbeat methods.

## Not decided here

Event envelopes, subscribers, model providers, memory, and tools. A reasoner is application code with a timeout. It is not permission to call a shell or a network API.
