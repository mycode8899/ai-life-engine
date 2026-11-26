# ADR 0001: Phase 1 core runtime

## Status

Accepted for 0.1.0. The runtime decisions still hold. The heartbeat package this record left unimplemented shipped in 0.2.0. See [ADR 0002](0002-heartbeat.md).

## Context

The project needs a runtime that can boot without a language model, survive a process restart, and leave room for a heartbeat, memory, and agent adapters. Those later layers must not become import-time dependencies.

## Decisions

1. **The public API is synchronous, plus one async wait.** `start`, `stop`, `pause`, `resume`, `status`, and `health` do their work and return. `run` waits until stop is requested. SQLite through the standard library is synchronous. An async wrapper around every call would not make the engine more correct, and it would make the common path harder to test. Phase 2 can schedule work inside `run` without changing the lifecycle methods.

2. **One engine identity per database.** Schema 1 uses a single-row table. Multiple engines can exist as multiple data directories. A later schema can relax this. The `StateStore` protocol does not hard-code the one-row rule beyond `load` returning the identity in that database.

3. **Optimistic concurrency uses a generation counter.** The record, audit row, and checkpoint are one commit. A stale writer fails instead of overlapping a newer state.

4. **Crash detection is "active record plus dead pid".** A clean `stop` sets `clean_shutdown`. Anything left in an active state with a pid that is gone is recovered on the next `start`. A live pid is never stolen. Failed runs are recovered the same way.

5. **SQLite is the only backend.** The protocol is real: tests run the engine against an in-memory store that is not part of the package. PostgreSQL is not stubbed.

6. **No placeholder packages.** Unimplemented layers are documented here and are not importable. Capability flags say what 0.1.0 actually does.

7. **The library does not configure logging unless asked.** `configure_logging` is for the CLI and for applications. Records go to the `ai_life_engine` logger.

8. **Secrets do not belong in metadata.** Keys that look like tokens, passwords, or API keys are rejected. The core never reads provider environment variables because it has no provider.

9. **Close does not invent a clean shutdown.** The destructor may close a SQLite handle the engine opened. It does not write `stopped`. A kill, or a dropped handle, stays visible as recovery work for the next start. Context managers and `stop()` are the clean path.

10. **Optional integrations are not declared until they exist.** There is no `openai`, `ollama`, or `server` extra. An empty extra would advertise support.

11. **Apache License 2.0.** It is the license for this repository.

12. **Version 0.1.0 is not a PyPI release.** Publishing is a separate step that has not been taken.

## Consequences

Applications can embed the runtime with a few calls and no network. Operators can inspect status after a crash. Contributors add a phase by implementing it and testing it, not by filling in a stub that already imported.

The sync core will need care once a heartbeat wants to mutate state from the event loop. The lock around mutations is the place that work has to go through.
