# AI Life Engine

Give AI agents a persistent life cycle.

AI Life Engine is an open-source, model-agnostic runtime for AI systems. Version 0.8.0 can initialize, start, pause, resume, stop, persist, and recover a process, tick a heartbeat, deliver events, store memory, call a selectable model provider, keep goals, run a granted tool through an agent adapter, load a plugin, and serve a local HTTP API. It can boot and keep running without a language model. No tool is granted at startup, and none of the built-in tools opens a shell.

This project builds persistent computational processes and state. It does not claim consciousness or subjective experience. It is not a personal assistant, a memory product, or a private automation tool.

The package has not been published to PyPI. Install it from a checkout.

## Why it exists

Most LLM applications still look like this:

```text
request → inference → response → end
```

That shape is right for a stateless call. It is a poor fit for a system that should stay available, remember that it was running, and come back after the process dies. AI Life Engine is the runtime underneath that longer life cycle:

```text
initialize → remain active → observe → evaluate → think → plan → act → remember → sleep → resume
```

The runtime shell, the heartbeat, the event bus, memory, a model interface, goal planning, a tool registry, a plugin loader, and a local HTTP API exist today. A heartbeat tick inspects state and, by default, does not call a model or a tool. An event can wake that loop without calling either one. Memory is stored text that survives a restart. A model is used only after the application selects a provider. A plan is stored text. `propose` is the only goal method that calls a model, and only when the application asks. A tool runs only after this process grants its permission. A plugin cannot grant that permission.

## How this differs from an agent framework

LangGraph, CrewAI, AutoGen, and similar projects orchestrate a task. They decide how an agent reasons and which tools it calls. AI Life Engine does not replace them.

| | Request-response app | Agent framework | AI Life Engine 0.8 |
| --- | --- | --- | --- |
| Unit of work | One prompt | One task or graph | One runtime process |
| Needs a model to boot | Usually | Usually | No. A provider can be selected later |
| Survives a restart | Rarely | Depends on the app | Runtime state is stored |
| Heartbeat | No | No | Optional. The default policy does not call a model |
| Events | The request itself | Usually in-process | A stored bus. Handlers run on `run`, and a delivery can wake a sleeping heartbeat |
| Memory | The prompt | Usually the app's database | Working, episodic, and semantic records in the runtime database |
| Goals | The caller's prompt | Usually the app | Stored goals, plans, reflections, and a next step |
| Tools | The caller's code | Framework tools | Registered tools. Denied until this process grants the permission |
| Role later | The caller | The worker | The life cycle around the worker |

An adapter layer that hosts an existing agent is in 0.7.0. It does not import a vendor framework. Plugins and the local API arrived in 0.8.0.

## Architecture

The target system has six layers. Version 0.8.0 ships the core runtime, the heartbeat, the event bus, memory, a model interface, goals, a tool registry, a plugin loader, a local HTTP API, and the storage interface, with SQLite as the only backend.

```text
LifeEngine
  ├── lifecycle state machine
  ├── heartbeat (off unless an interval is set)
  ├── event bus (handlers run while `run` is active)
  ├── memory (working, episodic, semantic)
  ├── model facade (no provider until one is selected)
  ├── goals, plans, and reflections
  ├── tools and agent adapter (nothing granted until allow)
  ├── plugins (loaded only when asked)
  ├── local HTTP API (loopback unless remote bind is explicit)
  ├── health checks
  ├── audit log and checkpoints
  └── StateStore
        └── SqliteStateStore
```

| Layer | Responsibility | Status |
| --- | --- | --- |
| 1. Core runtime | Start, stop, pause, resume, health, recovery, shutdown | Implemented |
| 2. Heartbeat | Periodic and signalled wake-up that does not imply a model call | Implemented in 0.2.0 |
| 3. Intelligence | Model, memory, goals, planning, reflection | Memory in 0.4.0. Model interface in 0.5.0. Goals, planning, and reflection in 0.6.0 |
| 4. Agent execution | Tools, permissions, framework adapters | Tool registry, permissions, and local adapters in 0.7.0. No vendor framework is imported |
| 5. Persistence | State, history, checkpoints | SQLite only |
| 6. Extensions | Providers and plugins | Storage, health checks, heartbeat hooks, event handlers, memory, models, goals, tools, and entry-point plugins in 0.8.0 |

`engine.heartbeat.wake()` is still a scheduler signal. The event bus is `engine.events`. A delivered event calls `wake("event")` only when a heartbeat interval is set.

Details and the decisions behind them are in [docs/architecture.md](docs/architecture.md), [docs/adr/0001-phase1-core-runtime.md](docs/adr/0001-phase1-core-runtime.md), [docs/adr/0002-heartbeat.md](docs/adr/0002-heartbeat.md), [docs/adr/0003-event-bus.md](docs/adr/0003-event-bus.md), [docs/adr/0004-memory.md](docs/adr/0004-memory.md), [docs/adr/0005-model-interface.md](docs/adr/0005-model-interface.md), [docs/adr/0006-goals.md](docs/adr/0006-goals.md), [docs/adr/0007-agents.md](docs/adr/0007-agents.md), and [docs/adr/0008-plugins-and-api.md](docs/adr/0008-plugins-and-api.md).

## Installation

Requires Python 3.12 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

On Windows, activate the virtual environment with `.venv\Scripts\activate`.

`pip install ai-life-engine` is the intended install once a release exists. It does not work today because the package is not on PyPI.

These extras are planned and are intentionally not defined yet, so they cannot be installed:

```text
ai-life-engine[openai]
ai-life-engine[ollama]
ai-life-engine[server]
```

The only runtime dependency is Pydantic. SQLite comes from the Python standard library.

## Quick start

```python
from pathlib import Path

from ai_life_engine import EngineConfig, LifeEngine

config = EngineConfig(data_dir=Path("./.ai-life-engine"), name="demo")

with LifeEngine(config) as engine:
    snapshot = engine.status()
    report = engine.health()
    print(snapshot.state.value)   # running
    print(report.status.value)    # healthy
    print(engine.capabilities["llm"])  # True. No provider is selected yet.
```

Leaving the `with` block stops the runtime and records a clean shutdown. A new process that opens the same data directory sees that identity again.

To tick while the process stays up, set an interval. The default policy never calls a reasoner:

```python
import asyncio
from pathlib import Path

from ai_life_engine import EngineConfig, LifeEngine

config = EngineConfig(
    data_dir=Path("./.ai-life-engine"),
    name="demo",
    heartbeat_interval=30,
)


async def main() -> None:
    engine = LifeEngine(config)
    try:
        await engine.run()
    finally:
        engine.close()


asyncio.run(main())
```

`run` without `heartbeat_interval` only waits for stop, and it still delivers events. Sleep and wake are on `engine.heartbeat`, and they are not the same thing as pause and resume.

To publish from another handle, initialize once, then subscribe inside `run`:

```python
engine.events.subscribe("ping", lambda event: print(event.payload))
engine.events.publish("ping", source="external", payload={"from": "cli"})
```

`publish` stores the event and returns. Handlers run on the `run` loop. They are not called from the publisher thread, and they are not stored. A second process can publish into the same database. The running loop polls for that row. See `examples/events.py`.

Memory is separate from the event log. It survives `close()` and a new process:

```python
engine.initialize()
engine.memory.remember_working("task", "review the log")
engine.memory.remember_episode("The runtime started without a model.")
engine.memory.remember_fact("project", "AI Life Engine stores memory in SQLite.")
hits = engine.memory.search("sqlite")
```

See `examples/memory.py`. Remembering does not call a model and does not wake the heartbeat.

To call a model, select a provider. The echo provider stays on this machine and returns the prompt:

```python
from ai_life_engine import open_model

engine.initialize()
engine.brain.use(open_model("echo"))
print(engine.brain.complete("ping").text)
```

`open_model("openai-compatible", base_url=..., model=...)` uses the same `complete` call. The lifecycle code does not change. See `examples/brain.py`. The default heartbeat policy still does not call the provider.

To keep a next step without calling a model:

```python
goal = engine.goals.add("Write notes", "Keep a durable next step.", priority=1)
engine.goals.plan(goal.goal_id, ["Draft the notes", "Revise the notes"])
action = engine.goals.next_action()
print(action.action if action is not None else "none")
engine.goals.reflect(goal.goal_id, outcome="succeeded", note="Drafted the notes.")
```

`next_action` returns text. It does not run a tool. `propose` asks the selected model for steps. See `examples/goals.py`.

To run a tool, grant its permission in this process and call it. After the plan above, one step is still pending. `echo` returns that step. `finish` marks it succeeded:

```python
from ai_life_engine import EchoAgent

engine.agents.allow("echo")
engine.agents.allow("goals.write")
engine.agents.use(EchoAgent())
print(engine.agents.step().result)
engine.agents.call("finish", "Drafted the notes.")
```

See `examples/agents.py`. Grants are not stored. A new process starts with every tool denied. The heartbeat does not call a tool.

The command line does the same work:

```bash
ai-life-engine init --data-dir ./.ai-life-engine --name demo
ai-life-engine status --data-dir ./.ai-life-engine
ai-life-engine health --data-dir ./.ai-life-engine
ai-life-engine run --data-dir ./.ai-life-engine
ai-life-engine run --data-dir ./.ai-life-engine --heartbeat-interval 30
ai-life-engine heartbeat --data-dir ./.ai-life-engine
ai-life-engine events --data-dir ./.ai-life-engine
ai-life-engine publish --data-dir ./.ai-life-engine --type ping --payload from=cli
ai-life-engine memory --data-dir ./.ai-life-engine
ai-life-engine remember --data-dir ./.ai-life-engine --kind fact --key project --content "Stored in SQLite."
ai-life-engine recall --data-dir ./.ai-life-engine --query sqlite
ai-life-engine brain --data-dir ./.ai-life-engine
ai-life-engine complete --data-dir ./.ai-life-engine --provider echo --prompt ping
ai-life-engine goal-add --data-dir ./.ai-life-engine --title "Write notes" --objective "Keep a next step"
ai-life-engine goal-next --data-dir ./.ai-life-engine
ai-life-engine agents --data-dir ./.ai-life-engine
ai-life-engine tool-call --data-dir ./.ai-life-engine --tool echo --argument ping --grant echo
```

`run` stays up until Ctrl+C or, on POSIX, SIGTERM. It then stops cleanly. `python -m ai_life_engine` is the same entry point. `--heartbeat-interval` is what turns ticking on.

## Examples

| Example | What it shows |
| --- | --- |
| [examples/quickstart.py](examples/quickstart.py) | Boot and health with no model configured |
| [examples/heartbeat.py](examples/heartbeat.py) | A few ticks with `reasoning_count=0` |
| [examples/events.py](examples/events.py) | One external event, no heartbeat, no model |
| [examples/memory.py](examples/memory.py) | Write memory, restart, and search it |
| [examples/brain.py](examples/brain.py) | Select the echo provider and switch to it again |
| [examples/goals.py](examples/goals.py) | Plan two steps and advance the next action |
| [examples/agents.py](examples/agents.py) | Grant echo and finish, then run one adapter step |
| [examples/plugins.py](examples/plugins.py) | Install a plugin and show its tool is still denied |
| [examples/recovery_after_restart.py](examples/recovery_after_restart.py) | Kill a process and recover it |

```bash
python examples/quickstart.py
python examples/heartbeat.py
python examples/events.py
python examples/memory.py
python examples/brain.py
python examples/goals.py
python examples/agents.py
python examples/plugins.py
python examples/recovery_after_restart.py
```

## Plugin development

These extension points work in 0.8.0:

- A custom `StateStore` can replace SQLite. Pass it to `LifeEngine(config, store=store)`. Heartbeat ticks also need `load_heartbeat`, `commit_heartbeat`, and `list_heartbeat_ticks`. Publishing needs the event methods in [docs/plugin_development.md](docs/plugin_development.md).
- A health check with a `name` and `run(snapshot)` can be registered on a live engine.
- A scheduler, reasoning policy, task source, and reasoner can be passed to `LifeEngine`. The default policy never requests reasoning.
- `engine.events.subscribe(type, handler)` registers an in-memory handler. `"*"` receives every type.
- `engine.memory` stores working memory, episodes, and facts. Search is a substring match.
- `engine.brain.use(provider)` selects a model. `complete` records usage and does not store the prompt.
- `engine.goals` stores goals, plans, and reflections. `next_action` does not execute the step.
- `engine.agents.register` adds a tool. `allow` grants its permission for this process. `use` selects an adapter. `call` and `step` are the only ways to run a tool.
- `engine.plugins.install(plugin)` or `engine.plugins.load()` sets up a plugin. Setup may register a tool, a health check, or an event handler. It cannot grant a permission.

The entry-point group is `ai_life_engine.plugins`. [docs/plugin_development.md](docs/plugin_development.md) is the contract.

## Local API

`serve` listens on `127.0.0.1:8765` unless you change the address. A non-loopback address requires `--allow-remote`. The API has no authentication. A request cannot grant a tool.

```bash
ai-life-engine serve --data-dir ./.ai-life-engine --grant echo
```

`GET /v1/health`, `/v1/status`, `/v1/goals`, `/v1/next-action`, `/v1/tools`, and `/v1/plugins` read state. `POST /v1/tools/call` uses grants already on that process. `POST /v1/events` stores an event.

The Docker image uses the same server and binds `0.0.0.0` with `--allow-remote`. Treat the container network as the boundary. The image is not published to a registry from this repository, and the package is not on PyPI. Tag pushes build a wheel and an sdist and upload them as a workflow artifact.

## Roadmap

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Core runtime, SQLite state, health, logging, tests | Implemented in 0.1.0 |
| 2 | Heartbeat, scheduler interface, sleep and wake | Implemented in 0.2.0 |
| 3 | Event bus and event-driven wake-up | Implemented in 0.3.0 |
| 4 | Working, episodic, and semantic memory | Implemented in 0.4.0 |
| 5 | Model provider interface and adapters | Implemented in 0.5.0 |
| 6 | Goals, planning, reflection, next action | Implemented in 0.6.0 |
| 7 | Agent adapters, tool registry, permissions | Implemented in 0.7.0 |
| 8 | Plugin SDK, REST API, Docker, release automation | Implemented in 0.8.0 |

A phase is documented as supported only after its tests pass. The heartbeat's `wake()` method is still only a signal to the scheduler. The default reasoning policy does not call a selected model. A heartbeat tick does not call a tool. Plugins are not loaded until `load` or `install`.

## Development

```bash
python -m pip install -e ".[dev]"
python -m ruff check src tests examples
python -m ruff format --check src tests examples
python -m pytest
```

Continuous integration runs those commands on Linux, macOS, and Windows for Python 3.12 and 3.13. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache License 2.0](LICENSE).
