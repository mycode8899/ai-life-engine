# API reference

This is the public surface of version 0.8.0. Import runtime types from `ai_life_engine`. Import storage types from `ai_life_engine.persistence`. Heartbeat types are also available from `ai_life_engine.heartbeat`. Event types are also available from `ai_life_engine.events`. Memory types are also available from `ai_life_engine.memory`. Model providers are also available from `ai_life_engine.brain`. Goal types are also available from `ai_life_engine.goals`. Tool and adapter types are also available from `ai_life_engine.agents`. Plugin types are available from `ai_life_engine.plugins`. The HTTP server is available from `ai_life_engine.api`.

Submodules may move. The names below are the compatibility surface.

## Package

```python
import ai_life_engine

ai_life_engine.__version__  # "0.8.0"
ai_life_engine.runtime_capabilities()
ai_life_engine.configure_logging("INFO")
```

`runtime_capabilities()` returns a new dict. Keys that are `False` are not implemented.

## EngineConfig

| Field | Meaning |
| --- | --- |
| `data_dir` | Directory for the database. Default `./.ai-life-engine`. The filesystem root is rejected. |
| `database_filename` | File name only. Default `engine.sqlite3`. |
| `name` | Display name written when this process claims the runtime. `None` keeps the stored name. |
| `engine_id` | UUID of an existing runtime. `None` adopts the identity already in the database, or creates one. |
| `metadata` | String map written on claim. `None` keeps the stored map. `{}` clears it. At most 32 entries. Secrets are rejected. |
| `heartbeat_interval` | Seconds between ticks inside `run`. `None` disables ticking. Range 0.01..86400. |
| `heartbeat_max_failures` | Consecutive tick failures before the heartbeat stops itself. Default 5. Range 1..100. |
| `event_poll_seconds` | How often a running engine looks for events published by another connection. Default 0.25. Range 0.05..60. In-process `publish` does not wait for this. |
| `event_handler_timeout` | Seconds one handler may run. Default 1. Range 0.05..30. |
| `model_timeout` | Seconds one model call may run. Default 30. Range 0.05..120. |

Unknown fields raise a Pydantic validation error.

## LifeEngine

```python
LifeEngine(
    config: EngineConfig | None = None,
    *,
    store: StateStore | None = None,
    scheduler: Scheduler | None = None,
    reasoning_policy: ReasoningPolicy | None = None,
    reasoner: Reasoner | None = None,
    task_source: TaskSource | None = None,
    reason_timeout: float = 5.0,
)
```

| Method | Behavior |
| --- | --- |
| `initialize()` | Create the identity if absent. Existing rows are not rewritten. |
| `start()` | Claim the runtime. Recovers a dead or failed previous run. |
| `pause()` | Pause. Only while this process is running it. |
| `resume()` | Resume from pause. |
| `stop(reason="requested")` | Clean stop when this process owns it. Otherwise a no-op. |
| `request_stop(reason="requested")` | Unblock `run`. Safe from another thread. |
| `run(install_signal_handlers=False, on_started=None)` | Async. Start if needed, then wait. Ticks when `heartbeat_interval` is set. Always delivers events. |
| `heartbeat` | Controller with `status`, `ticks`, `sleep`, and `wake`. |
| `events` | Bus with `subscribe`, `unsubscribe`, `publish`, and `history`. |
| `memory` | Working memory, episodes, facts, and `search`. |
| `brain` | Model facade with `use`, `complete`, `usage`, and `totals`. |
| `goals` | Goals, plans, reflections, and `next_action`. |
| `agents` | Tool registry, permissions, and the selected agent adapter. |
| `plugins` | Plugin loader. Nothing is loaded until `install` or `load`. |
| `status()` | Snapshot, refreshed from storage. |
| `health()` | Health report. Storage failures become an unhealthy report. |
| `register_health_check(check)` | Add a check. |
| `audit_trail(limit=100)` | Newest audit rows, limit 1..1000. |
| `checkpoints(limit=100)` | Newest checkpoints, limit 1..1000. |
| `close()` | Release a store this engine opened. Fails if the runtime is still owned. |

The context manager calls `start` and, on exit, `stop` and `close`. It does not tick. Ticks happen in `run`.

Properties: `config`, `engine_id`, `state`, `owns_runtime`, `capabilities`, `heartbeat`, `events`, `memory`, `brain`, `goals`, `agents`, `plugins`.

### Errors

| Exception | When |
| --- | --- |
| `ConfigurationError` | Bad paths, a mismatched engine id, a store that does not implement the protocol, a heartbeat interval on a store that cannot persist it, `publish` on a store that cannot persist events, memory calls on a store that cannot persist memory, or model calls on a store that cannot persist usage |
| `ModelError` | No provider, a timeout, an unreachable endpoint, a rejected response, or an invalid prompt. `code` is `not_configured`, `timeout`, `unavailable`, `rejected`, or `invalid_argument` |
| `GoalError` | Unknown id, invalid text, a full goal store, or a reflection the goal cannot accept. `code` is `not_found`, `invalid_argument`, `full`, or `conflict` |
| `AgentError` | No adapter, unknown tool, denied permission, a bad argument, or a failed tool. `code` is `not_configured`, `not_found`, `denied`, `rejected`, `unavailable`, `invalid_argument`, or `conflict` |
| `LifecycleError` | Illegal transition, closed handle, foreign runtime, bad limit or reason |
| `PersistenceError` | SQLite failures, schema mismatch, corrupt rows, generation conflicts |
| `pydantic.ValidationError` | Invalid `EngineConfig` or stored-record fields |

`LifecycleError.code` is one of `invalid_transition`, `already_running`, `not_running`, `foreign_runtime`, `closed`, `still_active`, `invalid_argument`, or `lifecycle`.

`PersistenceError.code` is one of `schema_mismatch`, `conflict`, `corrupt`, `unavailable`, `closed`, `missing`, `invalid`, `io`, or `persistence`.

## Heartbeat

`engine.heartbeat.status()` returns a `HeartbeatSnapshot`.

| Field | Meaning |
| --- | --- |
| `status` | `idle`, `beating`, `asleep`, or `failed` |
| `tick_count` | Successful ticks |
| `skipped_count` | Ticks skipped because the runtime was paused |
| `failure_count` / `consecutive_failures` | Tick errors. The consecutive count resets after a success |
| `reasoning_count` | Times a reasoner returned |
| `reasoning_skipped_count` | Policy asked for reasoning and no reasoner was configured |
| `asleep` | Interval ticks are suspended |
| `wake_pending` | A wake has not been consumed yet |
| `recovered` | This run found a loop that did not finish |
| `in_progress` | The loop is inside `run` and has not finished |
| `last_error` | Last tick or recovery error |

`sleep()` persists asleep when the runtime identity exists. `wake(reason="event")` requests one tick, clears sleep, and is safe from another thread. After `failed`, `wake` is what allows ticks again. Neither method pauses the lifecycle.

`ticks(limit=20)` returns newest-first history. The store keeps 50 rows.

`WakeReason` is `interval`, `event`, or `stop`. `ReasoningDecision` is `{required, reason}`. `NeverReasonPolicy` is the default. `IntervalScheduler` is the default wait.

`runtime_capabilities()["heartbeat"]`, `runtime_capabilities()["event_bus"]`, `runtime_capabilities()["memory"]`, `runtime_capabilities()["llm"]`, `runtime_capabilities()["goals"]`, `runtime_capabilities()["planning"]`, `runtime_capabilities()["reflection"]`, `runtime_capabilities()["agent_adapters"]`, `runtime_capabilities()["tool_registry"]`, `runtime_capabilities()["plugin_sdk"]`, and `runtime_capabilities()["rest_api"]` are `True`. `llm` means the interface exists. `engine.brain.configured` is false until `use`. Tool permissions start empty. Plugins are not loaded until asked.

## Agents

`register`, `allow`, `revoke`, and `use` do not need an identity. `call`, `step`, and `calls` do.

| Method | Behavior |
| --- | --- |
| `register(tool)` | Add a tool. The name must be unique. This does not grant it. |
| `tools()` | Registered tools, sorted by name. |
| `allow(permission)` / `revoke(permission)` | Change this process's grants. Not stored. |
| `grants()` | Sorted permission names. |
| `use(adapter)` | Select an adapter, or pass `None` to clear it. |
| `call(name, argument, agent="direct")` | Run one granted tool. |
| `step()` | Ask the adapter for one call. None means no tool ran. |
| `calls(limit=20)` | Newest tool-call rows. |

Built-in tools are `echo` (`echo`), `note` (`memory.write`), and `finish` (`goals.write`). `EchoAgent` chooses `echo` with the next-action text when that tool is granted. `ScriptedAgent([(tool, argument), ...])` returns those choices in order.

`AgentError`, `AgentStep`, `EchoAgent`, `EchoTool`, and `ScriptedAgent` are exported from `ai_life_engine`.

Arguments and results are 1..500 characters. A stored detail is clipped to 200. Names and permissions are lowercase, 1..64 characters.

## Plugins

`engine.plugins` does not need a running runtime. It does need an open handle.

| Method | Behavior |
| --- | --- |
| `install(plugin)` | Call `setup` on one object and return its record. |
| `load()` | Load every `ai_life_engine.plugins` entry point. |
| `records()` | Setup attempts in load order. Not stored. |

A plugin needs `name` matching `[a-z][a-z0-9._-]{0,63}` and `setup(context)`. The context methods are `register_tool`, `register_health_check`, and `subscribe`. A duplicate loaded name, a bad object, or an exception from `setup` returns a record with status `failed`.

## HTTP API

`create_server(engine, host="127.0.0.1", port=8765, allow_remote=False)` binds a `ThreadingHTTPServer`. `serve_until_stopped(server)` serves until SIGINT or SIGTERM. Pass `install_signals=False` when the caller is not the main thread.

| Request | Behavior |
| --- | --- |
| `GET /v1/health` | Health report. |
| `GET /v1/status` | Runtime snapshot. |
| `GET /v1/goals` | Up to 50 goals. |
| `GET /v1/next-action` | The next action, or `{"action": null}`. |
| `GET /v1/tools` | Registered tools. |
| `GET /v1/plugins` | Plugin records. |
| `POST /v1/tools/call` | JSON `{"name", "argument"}`. Uses grants already on this process. |
| `POST /v1/events` | JSON `{"type", "payload"}`. Payload values must be strings. |

A body larger than 32768 bytes is rejected. Unknown paths return 404. A denied tool returns 403. Other engine errors return 400. A request cannot grant a permission.

## Goals

`engine.goals` requires an existing runtime identity.

| Method | Behavior |
| --- | --- |
| `add(title, objective, priority=3)` | Create an active goal. Priority is 1..5. 1 is considered first. |
| `get(goal_id)` | Return one goal. |
| `list_goals(status=None, limit=20)` | Goals, most urgent first. |
| `set_status(goal_id, status)` | `active`, `blocked`, `done`, or `dropped`. |
| `plan(goal_id, steps)` | Replace the accepted plan. 1..8 steps. No model call. |
| `propose(goal_id)` | Ask the selected model for steps, then store that plan. |
| `plans(goal_id, limit=5)` | Plans for one goal, newest first. |
| `steps(plan_id)` | Steps in position order. |
| `next_action()` | Next pending step, or `None`. Not executed. |
| `reflect(goal_id, outcome=, note=, step_id=None)` | Record an outcome and maybe advance a step. |
| `reflections(goal_id=None, limit=20)` | Newest reflections. |

`Goal`, `NextAction`, `Plan`, `Reflection`, and `GoalError` are exported from `ai_life_engine`.

Titles are 1..80 characters. Objectives and notes are 1..500. Steps are 1..200. A runtime keeps 50 goals, 5 plans per goal, and 200 reflections.

## Models

`engine.brain` requires an existing runtime identity for `complete` and usage reads.

| Method | Behavior |
| --- | --- |
| `use(provider)` | Select a provider, or pass `None` to clear it. |
| `configured` | True after `use` with a provider. |
| `provider_name` / `model_name` | Names from the selected provider, or `None`. |
| `complete(prompt, system=None, temperature=None, max_tokens=None)` | Call the provider and store usage. |
| `usage(limit=20)` | Newest usage rows. |
| `totals()` | Request count, error count, and token sums. |

`open_model("echo")` returns the prompt. `open_model("openai-compatible", base_url=..., model=..., api_key=None)` POSTs to `{base_url}/chat/completions`. `ScriptedProvider(replies)` returns each reply once. `EchoProvider`, `ModelResponse`, `TokenUsage`, `ModelError`, and `open_model` are exported from `ai_life_engine`.

Prompts are 1..8000 characters. A system prompt is 1..2000. `temperature` is 0..2. `max_tokens` is 1..4096. Usage rows do not include the prompt.

## Memory

`engine.memory` requires an existing runtime identity.

| Method | Behavior |
| --- | --- |
| `remember_working(key, value, payload=None)` | Insert or replace one scratchpad entry. At most 32 keys. |
| `recall_working(key)` | One entry, or `None`. |
| `working()` | Every working entry, newest update first. |
| `forget_working(key)` | Delete one key. A missing key raises. |
| `clear_working()` | Delete every working entry. |
| `remember_episode(summary, source="application", payload=None)` | Append one episode. The newest 200 are kept. |
| `episodes(limit=20)` | Newest episodes first. |
| `remember_fact(key, content, payload=None)` | Insert or replace one fact. The fact id stays stable. At most 200 facts. |
| `recall_fact(key)` | One fact, or `None`. |
| `facts(limit=20)` | Facts, newest update first. |
| `forget_fact(key)` | Delete one fact. A missing key raises. |
| `search(query, kind=None, limit=20)` | Case-insensitive substring. `kind` is `working`, `episode`, `fact`, or omitted. |
| `counts()` | `working`, `episodes`, and `facts` counts. |

Keys and sources are 1..64 lowercase names. Text is printable and at most 500 characters. A search query is at most 200 characters. Payloads are string maps and reject secret-looking keys.

`MemoryKind`, `WorkingMemory`, `Episode`, `Fact`, `MemoryHit`, and `MemoryCounts` are exported from `ai_life_engine`.

## Events

`engine.events` is an `EventBus`.

| Method | Behavior |
| --- | --- |
| `subscribe(event_type, handler)` | Register a callable. Returns a subscription id. `"*"` receives every type. At most 100 subscriptions. |
| `unsubscribe(subscription_id)` | Remove one subscription. An unknown id raises `LifecycleError`. |
| `publish(event_type, *, source="external", payload=None)` | Store a pending `EngineEvent` and return it. Requires an existing identity. Does not call handlers itself. |
| `history(limit=20)` | Newest events first. Limit 1..1000. |

`event_type` and `source` are 1..64 characters: a lowercase letter, then lowercase letters, digits, `.`, `_`, or `-`. `payload` is a string map of at most 32 entries. Secret-looking keys are rejected. More than 100 pending events raises `LifecycleError`.

`EngineEvent` fields: `id`, `event_id`, `engine_id`, `type`, `source`, `payload`, `status`, `handler_error`, `created_at`, `delivered_at`. `EventStatus` is `pending`, `delivered`, or `failed`.

Handlers run on the `run` loop. A timeout or exception marks that event `failed` and does not stop the runtime. No matching subscriber leaves the row pending. Delivery happens once. Subscriptions are not persisted.

`EngineEvent` and `EventStatus` are exported from `ai_life_engine`.

## RuntimeSnapshot

Frozen model. Useful fields: `engine_id`, `name`, `state`, `persisted_state`, `run_id`, `pid`, `started_at`, `updated_at`, `stopped_at`, `clean_shutdown`, `recovery_required`, `blocked_by_live_process`, `recovery_count`, `pause_count`, `software_version`, `metadata`, `owns_runtime`, `data_dir`.

`state` is this handle. `persisted_state` is the database. Before start they can differ.

## Health

`HealthStatus`: `healthy`, `degraded`, `unhealthy`, `inactive`.

`CheckStatus`: `pass`, `warn`, `fail`.

`HealthReport` includes `status`, identity, state, ownership, recovery flags, `uptime_seconds`, `checked_at`, and `checks`.

A health check is an object with string `name` and `run(snapshot) -> CheckResult`.

## LifecycleState

`created`, `starting`, `running`, `pausing`, `paused`, `resuming`, `stopping`, `stopped`, `failed`, `recovering`.

The allowed edges live in `ai_life_engine.core.lifecycle.ALLOWED_TRANSITIONS`. That table is covered by tests. Prefer the engine methods over driving the table yourself.

## Persistence

```python
from ai_life_engine.persistence import SqliteStateStore, StateStore
```

Records: `EngineRecord`, `NewAudit`, `AuditEntry`, `NewCheckpoint`, `Checkpoint`, `CommittedState`.

Schema constant: `SCHEMA_VERSION` is `"7"`. Versions `"1"` through `"6"` are upgraded by adding the missing heartbeat, event, memory, model-usage, goal, and tool-call tables. Any other version raises `PersistenceError` with code `schema_mismatch`.

## CLI

```text
ai-life-engine version
ai-life-engine init --data-dir DIR [--name NAME] [--engine-id UUID] [--json]
ai-life-engine status --data-dir DIR [--json]
ai-life-engine health --data-dir DIR [--json] [--expect {healthy,running}]
ai-life-engine heartbeat --data-dir DIR [--json]
ai-life-engine events --data-dir DIR [--json] [--limit N]
ai-life-engine publish --data-dir DIR --type TYPE [--source NAME] [--payload KEY=VALUE]
ai-life-engine memory --data-dir DIR [--json]
ai-life-engine remember --data-dir DIR --kind {working,episode,fact} [--key KEY] [--value TEXT] [--summary TEXT] [--content TEXT] [--source NAME] [--payload KEY=VALUE]
ai-life-engine recall --data-dir DIR --query TEXT [--kind {working,episode,fact}] [--limit N] [--json]
ai-life-engine brain --data-dir DIR [--json]
ai-life-engine complete --data-dir DIR --prompt TEXT [--provider {echo,openai-compatible}] [--base-url URL] [--model NAME]
ai-life-engine goals --data-dir DIR [--json]
ai-life-engine goal-add --data-dir DIR --title TEXT --objective TEXT [--priority N]
ai-life-engine goal-plan --data-dir DIR --goal-id UUID --step TEXT [--step TEXT]
ai-life-engine goal-next --data-dir DIR
ai-life-engine goal-reflect --data-dir DIR --goal-id UUID --outcome OUTCOME --note TEXT [--step-id UUID]
ai-life-engine agents --data-dir DIR [--json]
ai-life-engine tool-call --data-dir DIR --tool NAME --argument TEXT [--grant PERMISSION]
ai-life-engine plugins --data-dir DIR [--load] [--json]
ai-life-engine serve --data-dir DIR [--host HOST] [--port PORT] [--grant PERMISSION] [--load-plugins] [--allow-remote]
ai-life-engine run --data-dir DIR [--name NAME] [--heartbeat-interval SECONDS] [--log-level LEVEL]
```

| Exit | Meaning |
| --- | --- |
| 0 | Success. For `health`, also `inactive` unless `--expect` says otherwise. |
| 1 | Health is degraded, unhealthy, or missed `--expect`. |
| 2 | Configuration, lifecycle, or persistence error. Bad arguments from argparse also use 2. |
| 130 | Interrupted. |

`run` prints a `running` line after the claim and a `stopped` line after a clean return. `serve` prints `listening=host:port` before it accepts requests and a `stopped` line after the server returns. `plugins` without `--load` prints `plugins=none` when nothing has been installed in that process.
