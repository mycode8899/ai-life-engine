# Changelog

All notable changes to AI Life Engine are documented in this file.

The format is based on Keep a Changelog, and the project uses Semantic Versioning. This version has not been published to PyPI.

## [Unreleased]

## [0.8.0] - 2026-11-26

### Added

- Plugin loader on `engine.plugins`. `install` sets up one object. `load` reads the `ai_life_engine.plugins` entry-point group. Neither runs at import or at engine construction.
- A plugin `setup(context)` may register a tool, a health check, or an event handler. It cannot grant a permission. A failed setup is recorded and the process keeps running.
- A `plugins` health check. No plugins passes. A failed setup warns.
- A local HTTP API on the standard library. The default bind is `127.0.0.1:8765`. A non-loopback address requires `allow_remote`. There is no authentication, and a request cannot grant a tool.
- CLI `plugins` and `serve`.
- `examples/plugins.py`, a `Dockerfile`, and a release workflow that builds a wheel and an sdist. The workflow does not publish to PyPI.

### Not included

- A new database schema. Plugin records stay in memory.
- Authentication on the HTTP API.
- A shell tool, filesystem tool, or network tool.
- Vendor agent frameworks.
- Publishing the package to PyPI.

## [0.7.0] - 2026-11-26

### Added

- Agent facade on `engine.agents`: `register`, `allow`, `revoke`, `use`, `call`, and `step`.
- Built-in tools `echo`, `note`, and `finish`. They are registered and denied until this process grants `echo`, `memory.write`, or `goals.write`. Grants are not stored.
- Adapters `EchoAgent` and `ScriptedAgent`. `use` swaps them without changing lifecycle code. `step` asks the adapter for one tool and still checks permission. The heartbeat does not call `step`.
- A tool-call log in SQLite. Schema versions 1 through 6 upgrade to version 7. The newest 200 calls are kept. Arguments are not stored. A clipped result or error is.
- An `agents` health check. No calls passes. A denied or failed call warns.
- CLI `agents` and `tool-call`. `--grant` applies only to that command.
- `examples/agents.py`.

### Not included

- A shell tool, filesystem tool, or network tool.
- Vendor agent frameworks.
- The plugin SDK, REST API, and Docker packaging.
- Automatic tool calls from a heartbeat tick.

## [0.6.0] - 2026-11-26

### Added

- Goal facade on `engine.goals`: `add`, `plan`, `propose`, `next_action`, `reflect`, and status changes.
- SQLite tables for goals, plans, steps, and reflections. Schema versions 1 through 5 upgrade to version 6. A runtime keeps 50 goals, 5 plans per goal, and 200 reflections. A new plan supersedes the previous accepted plan.
- `next_action` selects the pending step of the most urgent active goal. It does not execute the step and does not call a model.
- `propose` asks the selected model for steps and stores them. It is never called by the heartbeat.
- A reflection of `succeeded` or `skipped` advances a step. The goal becomes `done` when no pending step remains. `blocked` marks the goal blocked. `failed` and `noted` only record the note.
- The default heartbeat task source counts pending goal steps. With no goals it still reports that nothing is waiting. The default reasoning policy still does not call a model.
- A `goals` health check. No goals passes. A blocked goal or an active goal without a plan warns.
- CLI `goals`, `goal-add`, `goal-plan`, `goal-next`, and `goal-reflect`.
- `examples/goals.py`.

### Not included

- Tool execution, permissions, agent adapters, and the plugin SDK.
- Automatic planning on a heartbeat tick.
- Embeddings or a vendor SDK.

## [0.5.0] - 2026-11-26

### Added

- Model facade on `engine.brain`: `use`, `complete`, `usage`, and `totals`.
- Providers `echo`, `scripted`, and `openai-compatible`. The HTTP provider speaks chat-completions JSON and does not import a vendor SDK. Pass `api_key` to the provider. The library does not read the environment.
- Usage rows in SQLite. Schema versions 1 through 4 upgrade to version 5. The newest 200 calls are kept. Prompts are not stored.
- A heartbeat tick calls the selected provider only when the reasoning policy sets `required=True` and no reasoner was supplied. The default policy still does not.
- A `brain` health check. No calls passes. A failed call warns and leaves the runtime up.
- CLI `brain` and `complete`. `complete --provider openai-compatible` reads `AI_LIFE_ENGINE_API_KEY` when it is set.
- `examples/brain.py`.

### Not included

- Goals, planning, reflection, tools, and the plugin SDK.
- Vendor packages for OpenAI, Anthropic, or Ollama. One HTTP adapter covers compatible servers.
- Automatic model calls from `run` when no provider is selected.
- Optional extras that install those vendor packages.

## [0.4.0] - 2026-11-26

### Added

- Memory on `engine.memory`: working key-value entries, episodic records, and semantic facts.
- `search` across those stores. Matching is a case-insensitive substring. It does not call a model or use embeddings.
- Persistent memory in SQLite. Schema version 1, 2, and 3 databases upgrade to version 4. Working memory holds 32 keys. Facts hold 200 keys. Episodes keep the newest 200 rows.
- A `memory` health check. An empty store passes, and stored memory passes, so a healthy runtime stays healthy.
- CLI `memory`, `remember`, and `recall`.
- `examples/memory.py`.

### Not included

- Model adapters, goals, planning, reflection, tools, and the plugin SDK.
- Vector search, embeddings, and automatic summarization.
- Remembering does not publish an event and does not wake the heartbeat.
- Optional extras for OpenAI, Ollama, or the HTTP server.

## [0.3.0] - 2026-11-26

### Added

- Internal event bus on `engine.events`: `subscribe`, `unsubscribe`, `publish`, and `history`.
- Handlers run on the `run` loop. A delivery wakes a sleeping heartbeat when `heartbeat_interval` is set. With no interval, the event is still delivered and `tick_count` stays 0.
- Persistent events in SQLite. Schema version 1 and 2 databases upgrade to version 3. Pending events wait for a subscriber. Delivered and failed rows are capped at 100.
- A failed or timed-out handler marks that event `failed` and leaves the runtime up. The `events` health check warns. It does not fail the runtime.
- CLI `events` and `publish`.
- `examples/events.py`.

### Not included

- Memory, model adapters, goals, planning, tools, and the plugin SDK.
- Persisted subscriptions. Handlers are in memory for the life of the process.
- Automatic lifecycle events, retries, and a replay of events that were already delivered.
- Optional extras for OpenAI, Ollama, or the HTTP server.

## [0.2.0] - 2026-11-26

### Added

- Heartbeat loop inside `run` when `EngineConfig.heartbeat_interval` is set. Without an interval, `run` still only waits.
- `IntervalScheduler`, `NeverReasonPolicy`, and `EmptyTaskSource`. A tick does not call a reasoner unless a policy sets `required=True` and a reasoner is configured.
- `engine.heartbeat.sleep()`, `wake()`, `status()`, and `ticks()`. Sleep is persisted across restarts. Wake is a signal, not an event bus.
- Persistent heartbeat state in SQLite. Schema version 1 databases upgrade to version 2. Tick history keeps the newest 50 rows.
- Recovery of an in-progress heartbeat, and a failure cap that stops ticking without stopping the runtime.
- A `heartbeat` health check. It passes when the loop is idle, so existing healthy runtimes stay healthy.
- CLI `heartbeat` and `run --heartbeat-interval`.
- `examples/heartbeat.py`.

### Not included

- An event bus. Phase 3 has not been started.
- Memory, model adapters, goals, planning, tools, and the plugin SDK.
- Optional extras for OpenAI, Ollama, or the HTTP server.

## [0.1.0] - 2026-11-26

### Added

- Phase 1 core runtime: initialize, start, pause, resume, stop, and close, with no language model required.
- SQLite persistence for one runtime identity per database, including generation checks, checkpoints, and an audit log.
- Crash recovery when a previous process left the runtime active or failed.
- Health reports with storage, lifecycle, and consistency checks, plus caller-registered checks.
- An async `run` loop that waits until stop is requested and does not call a model.
- A command-line interface: `version`, `init`, `status`, `health`, and `run`.
- Architecture notes, an API reference, and a CI workflow for Python 3.12 and 3.13.

### Not included

- Heartbeat, event bus, memory, model adapters, goals, planning, tools, and the plugin SDK.
- Optional extras for OpenAI, Ollama, or the HTTP server.
