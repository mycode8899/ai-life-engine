# Plugin development

Version 0.8.0 loads a plugin only when the application calls `engine.plugins.install` or `engine.plugins.load`. There is no base class. An entry point in the group `ai_life_engine.plugins` points at an object, or a class, with `name` and `setup(context)`. This document is that contract, plus the extension points that already exist.

## What you can extend today

### Storage

Implement the methods on `ai_life_engine.persistence.StateStore`:

- `load(engine_id)`
- `commit_state(record, expected_generation=..., audit=..., checkpoint=...)`
- `list_audit(engine_id, limit=...)`
- `list_checkpoints(engine_id, limit=...)`
- `ping()`
- `close()`

`commit_state` must be atomic. `expected_generation=None` inserts the first record. Any other value updates that generation or fails with `PersistenceError` and code `conflict`.

Pass the object in:

```python
engine = LifeEngine(config, store=my_store)
```

The engine does not close an injected store. Close it yourself after the engine is stopped and closed. SQLite is the only store shipped in the package. An in-memory store exists in the test suite as a contract check. It is not a supported backend.

If `heartbeat_interval` is set, the store also needs:

- `load_heartbeat(engine_id)`
- `commit_heartbeat(record, expected_generation=..., tick=...)`
- `list_heartbeat_ticks(engine_id, limit=...)`

`commit_heartbeat` must be atomic, including the optional tick row. `expected_generation=None` inserts the first heartbeat row. A store without these methods can still boot the runtime when the interval is left unset.

Publishing needs:

- `append_event(event_id=..., engine_id=..., event_type=..., source=..., payload=..., created_at=...)`
- `list_events(engine_id, limit=...)`
- `list_pending_events(engine_id, limit=...)`
- `count_pending_events(engine_id)`
- `mark_event(engine_id, event_id, status=..., handler_error=..., delivered_at=...)`
- `latest_event(engine_id)`

`append_event` returns the stored `EngineEvent` and must not drop pending rows when it trims history. A store without these methods can still boot. `publish` raises `ConfigurationError` until they exist. `run` simply does not deliver.

Remembering and searching need:

- `upsert_working`, `get_working`, `list_working`, `delete_working`, `clear_working`, `count_working`
- `append_episode`, `list_episodes`, `count_episodes`
- `upsert_fact`, `get_fact`, `list_facts`, `delete_fact`, `count_facts`

`upsert_working` and `upsert_fact` replace by `(engine_id, key)` and must keep the row id. `upsert_fact` must also keep `fact_id` and `created_at`. `append_episode` may delete older rows after the newest 200. A store without these methods can still boot. Memory calls raise `ConfigurationError` until they exist.

Model usage needs `append_usage`, `list_usage`, `latest_usage`, and `usage_totals`. A store without them can still boot. `complete` raises `ConfigurationError` before it calls the provider.

Goals need `count_goals`, `add_goal`, `get_goal`, `list_goals`, `update_goal_status`, `replace_plan`, `accepted_plan`, `list_plans`, `list_steps`, `apply_reflection`, and `list_reflections`. A store without them can still boot. Goal calls raise `ConfigurationError`. `replace_plan` must supersede the previous accepted plan in one transaction.

Tool calls need `append_tool_call`, `list_tool_calls`, and `latest_tool_call`. A store without them can still boot. `call` raises `ConfigurationError` before the tool runs.

### Health checks

```python
from ai_life_engine import CheckResult, CheckStatus

class Budget:
    name = "budget"

    def run(self, snapshot):
        return CheckResult(name="budget", status=CheckStatus.PASS, message="within budget")

engine.register_health_check(Budget())
```

Rules:

- `name` is a Python identifier.
- `storage`, `lifecycle`, `consistency`, `heartbeat`, `events`, `memory`, `brain`, `goals`, and `agents` are reserved.
- `run` receives a `RuntimeSnapshot` and returns a `CheckResult`.
- A check must return quickly.
- A check must not call `start`, `stop`, `pause`, `resume`, `health`, or `close`. Those calls are rejected, and the check is reported as failed.
- An exception inside `run` fails that check. The runtime stays in its current state.

A warning makes a running engine `degraded`. A failure makes the report `unhealthy`.

### Heartbeat hooks

Pass any of these to `LifeEngine`. Each one is optional.

| Argument | Method | When it runs |
| --- | --- | --- |
| `scheduler` | `async wait_for_wake(interval_seconds, wake, stop)` | Instead of the built-in interval wait. `interval_seconds=None` means wait for wake or stop, and must not spin. |
| `reasoning_policy` | `decide(observation) -> ReasoningDecision` | Every tick that is not skipped. The default returns `required=False`. |
| `task_source` | `inspect(snapshot) -> TaskReport` | Every tick, before the policy. The default reports zero tasks. |
| `reasoner` | `reason(observation) -> str` | Only when the policy sets `required=True`. Runs in a worker thread. |
| `reason_timeout` | Seconds, default 5, range 0.05..120 | Bounds `reason`. A timeout fails that tick. |

`decide` and `inspect` run while the runtime lock is held. They must return quickly and must not call `start`, `stop`, `pause`, `resume`, `run`, `health`, or `close`. `reason` runs outside the lock. It must not call those methods either.

A policy that asks for reasoning when no reasoner is configured does not fail the tick. The tick is stored with `reasoning_executed=false` and `reasoning_skipped_count` increases. Nothing in the core imports a model client.

### Event handlers

```python
def on_ping(event):
    print(event.type, event.payload)

subscription_id = engine.events.subscribe("ping", on_ping)
engine.events.unsubscribe(subscription_id)
```

Rules:

- Call `subscribe` before or during `run`. Handlers are invoked only while `run` is active.
- A handler is `Callable[[EngineEvent], None]`. It runs on a worker thread for at most `event_handler_timeout` seconds.
- Do not call `start`, `stop`, `pause`, `resume`, `run`, `health`, or `close` from a handler. `request_stop` is safe.
- An exception or a timeout marks that event `failed`. Other subscriptions and the runtime continue.
- `"*"` receives every type. At most 100 subscriptions.
- Subscriptions are not stored. Register them again after a restart. Pending events are.

`publish` can be called from another thread or another process that shares the data directory. It does not call the handler itself.

### Memory

```python
engine.memory.remember_working("task", "review the log")
engine.memory.remember_episode("The runtime started without a model.")
engine.memory.remember_fact("project", "Stored in SQLite.")
hits = engine.memory.search("sqlite", kind="fact")
```

Rules:

- Call `initialize` or `start` before the first write.
- Keys are lowercase names. Text is one printable line.
- Do not put secrets in values or payloads. Secret-looking payload keys are rejected.
- A full working store or fact store raises. It does not drop an older key.
- Search does not call a model. There is no embedding hook in this version.

### Model providers

```python
from ai_life_engine import open_model

engine.brain.use(open_model("echo"))
engine.brain.complete("ping")
engine.brain.use(open_model("openai-compatible", base_url="http://127.0.0.1:11434/v1", model="llama3"))
```

A provider is any object with `provider_name`, `model_name`, and `complete(request, timeout) -> ModelResponse`. `use` is the only registration step. Do not import a vendor SDK from the core package. Pass an API key as `api_key`. Do not put it in metadata, a prompt, or a base URL.

### Goals

```python
goal = engine.goals.add("Write notes", "Keep a durable next step.")
engine.goals.plan(goal.goal_id, ["Draft the notes"])
engine.goals.next_action()
```

Pass your own `task_source` to `LifeEngine` if a tick should not count pending steps. Do not put secrets in titles, objectives, steps, or notes. Do not treat `next_action` as a command to run.

### Tools

```python
class Shout:
    name = "shout"
    description = "Uppercase the argument."
    permission = "shout"

    def call(self, argument: str) -> str:
        return argument.upper()

engine.agents.register(Shout())
engine.agents.allow("shout")
engine.agents.call("shout", "quiet")
```

A tool receives one string and returns one string. Do not open a shell, and do not read credentials from the environment inside a tool shipped in this package. Grant only the permission that tool declares. `use` selects an adapter with `name` and `decide(request) -> AgentDecision | None`.

## Plugins

A plugin is an object with a lowercase `name` and `setup(context)`. `install` accepts that object. `load` finds installed entry points and calls `setup` on each one.

```python
class Ping:
    name = "ping"
    description = "Return pong."
    permission = "ping"

    def call(self, argument: str) -> str:
        return "pong"


class Sample:
    name = "sample"

    def setup(self, context) -> None:
        context.register_tool(Ping())


engine.plugins.install(Sample())
```

The same class can be published as an entry point:

```toml
[project.entry-points."ai_life_engine.plugins"]
sample = "my_package.plugins:Sample"
```

`context` has three methods:

| Method | Effect |
| --- | --- |
| `register_tool(tool)` | Add a tool. It stays denied. |
| `register_health_check(check)` | Add a check whose name is not reserved. |
| `subscribe(event_type, handler)` | Register an in-memory event handler. |

`setup` runs outside the engine lock so it can call those methods. It cannot call `allow`, `call`, `start`, or `stop`. A raised exception becomes a failed record. The runtime stays up, and the `plugins` health check warns. Records are not written to SQLite. Loading the same name twice records a failure and does not call `setup` again.

`examples/plugins.py` installs a plugin in-process and shows that its tool is denied.

## What you cannot extend yet

- Vendor agent frameworks inside the core package
- A shell, filesystem, or network tool in the core package
- Granting a permission from plugin setup

Do not add an import of OpenAI, Anthropic, Ollama, LangGraph, CrewAI, AutoGen, FastAPI, Redis, or a PostgreSQL driver to the core. The built-in HTTP adapter is the supported way to reach a compatible server. The local API uses the standard library. A vendor SDK belongs in an optional extra, and only after tests cover it.
