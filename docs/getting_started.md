# Getting started

## Install from a checkout

Python 3.12 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Confirm the console script:

```bash
ai-life-engine version
```

The package is not on PyPI. A checkout install is the supported path.

## Create a runtime

```python
from pathlib import Path

from ai_life_engine import EngineConfig, LifeEngine

config = EngineConfig(data_dir=Path("./.ai-life-engine"), name="demo")
engine = LifeEngine(config)
engine.initialize()
print(engine.status().engine_id)
engine.close()
```

`initialize()` writes an identity in the stopped state. It is optional. `start()` creates the identity if it is missing.

`name` and `metadata` are applied when omitted as "keep what is stored". Set them to replace the stored values on the next claim. Do not put API keys in metadata. Those keys are rejected.

## Run it

```python
with LifeEngine(config) as engine:
    assert engine.health().status.value == "healthy"
```

Or stay running until a signal:

```bash
ai-life-engine run --data-dir ./.ai-life-engine --name demo
```

Ctrl+C, and SIGTERM on POSIX, call a clean stop. The database then has `persisted_state=stopped` and `clean_shutdown=true`.

## Heartbeat

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

Or from the shell:

```bash
ai-life-engine run --data-dir ./.ai-life-engine --heartbeat-interval 30
ai-life-engine heartbeat --data-dir ./.ai-life-engine
```

`heartbeat_interval` is required for ticks. Omit it and `run` only waits. The default policy does not call a reasoner, so `reasoning_count` stays 0.

```python
engine.heartbeat.sleep()          # no interval ticks until wake
engine.heartbeat.wake("inbox")    # one tick, then the interval resumes
```

Sleep is stored. A new process stays asleep until `wake`. Pause is separate: a paused runtime skips ticks but does not forget the interval. After several consecutive tick failures the heartbeat stops itself and the process keeps running until you stop it or call `wake`.

See `examples/heartbeat.py` for a short run that prints the tick count.

## Events

An event can arrive without a heartbeat interval and without a model:

```python
engine.events.subscribe("ping", lambda event: print(event.payload))
engine.events.publish("ping", source="external", payload={"from": "outside"})
```

`publish` returns as soon as the row is stored. The handler runs on the `run` loop, not on the caller. Leave `run` alive until the handler has been invoked. `tick_count` stays 0 when no interval is set. Set `heartbeat_interval` when a delivery should also wake a tick. The default policy still does not call a reasoner.

From another shell, after `init`:

```bash
ai-life-engine publish --data-dir ./.ai-life-engine --type ping --payload from=cli
ai-life-engine events --data-dir ./.ai-life-engine
```

A running `run` picks that row up on its poll interval. See `examples/events.py`.

## Memory

```python
engine.initialize()
engine.memory.remember_working("task", "review the log")
engine.memory.remember_episode("The runtime started without a model.")
engine.memory.remember_fact("project", "AI Life Engine stores memory in SQLite.")
for hit in engine.memory.search("sqlite"):
    print(hit.kind.value, hit.text)
```

Close the engine and open the same data directory. `recall_working`, `recall_fact`, and `search` return the same records. No model is configured, and `tick_count` stays 0 unless you also set a heartbeat interval.

```bash
ai-life-engine remember --data-dir ./.ai-life-engine --kind fact --key project --content "Stored in SQLite."
ai-life-engine recall --data-dir ./.ai-life-engine --query sqlite
ai-life-engine memory --data-dir ./.ai-life-engine
```

See `examples/memory.py`.

## Models

```python
from ai_life_engine import open_model

engine.initialize()
engine.brain.use(open_model("echo"))
response = engine.brain.complete("ping")
print(response.text)
```

`echo` returns the prompt and records usage. Switch providers with another `use` call. The same `complete` method talks to an HTTP server:

```python
engine.brain.use(
    open_model(
        "openai-compatible",
        base_url="http://127.0.0.1:11434/v1",
        model="llama3",
    )
)
```

No provider is selected until `use`. A heartbeat still skips the model unless you install a reasoning policy that sets `required=True`. See `examples/brain.py`.

## Goals

```python
goal = engine.goals.add("Write notes", "Keep a durable next step.", priority=1)
engine.goals.plan(goal.goal_id, ["Draft the notes", "Revise the notes"])
action = engine.goals.next_action()
engine.goals.reflect(goal.goal_id, outcome="succeeded", note="Drafted the notes.")
```

`plan` does not call a model. `next_action` returns the step and does not run it. `propose(goal_id)` is the call that asks the selected model for steps. See `examples/goals.py`.

## Tools

```python
from ai_life_engine import EchoAgent

engine.agents.allow("echo")
engine.agents.use(EchoAgent())
step = engine.agents.step()
```

`allow` lasts for this process. `step` calls `echo` with the pending step when one exists and `echo` is granted. `finish` needs `goals.write` and marks that step succeeded. See `examples/agents.py`.

```bash
ai-life-engine tool-call --data-dir ./.ai-life-engine --tool echo --argument ping --grant echo
ai-life-engine agents --data-dir ./.ai-life-engine
```

## Plugins and the local API

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

`setup` can register a tool. The tool stays denied until this process calls `allow`. See `examples/plugins.py`.

```bash
ai-life-engine plugins --data-dir ./.ai-life-engine
ai-life-engine serve --data-dir ./.ai-life-engine --port 8765 --grant echo
```

`serve` listens on loopback. Add `--allow-remote` only when you intend another machine to reach it. The API has no authentication.

```bash
ai-life-engine goal-add --data-dir ./.ai-life-engine --title "Write notes" --objective "Keep a next step"
ai-life-engine goal-next --data-dir ./.ai-life-engine
```

```bash
ai-life-engine complete --data-dir ./.ai-life-engine --provider echo --prompt ping
ai-life-engine brain --data-dir ./.ai-life-engine
```

## Pause

```python
engine = LifeEngine(config)
engine.start()
engine.pause()
engine.resume()
engine.stop()
engine.close()
```

Pause keeps the run id. Stop ends it. The next start creates another run id.

## Inspect

```bash
ai-life-engine status --data-dir ./.ai-life-engine --json
ai-life-engine health --data-dir ./.ai-life-engine --json
```

`health` exits 0 when the report is `healthy` or `inactive`. It exits 1 when the report is `degraded` or `unhealthy`. Add `--expect running` when a probe should fail unless this command actually started the runtime and found it healthy. A status command against a stopped database is inactive, which is not a failure.

## Recover

If a process is killed while running, the next `start()` on that data directory adopts the same engine id, increments `recovery_count`, and logs a recovery. See `examples/recovery_after_restart.py`.

A second process cannot start the runtime while the first pid is still alive.

## Logging

```python
from ai_life_engine import configure_logging

configure_logging("INFO")
```

Without that call, the library emits records on the `ai_life_engine` logger and leaves handler setup to the application. The CLI configures logging itself.

## Tests

```bash
python -m pytest
```
