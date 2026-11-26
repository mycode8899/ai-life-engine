# SPDX-License-Identifier: Apache-2.0
"""Command-line interface for the runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from ai_life_engine.brain.providers import open_model
from ai_life_engine.core.config import EngineConfig
from ai_life_engine.core.engine import LifeEngine
from ai_life_engine.core.health import HealthReport, HealthStatus
from ai_life_engine.core.state import LifecycleState, RuntimeSnapshot
from ai_life_engine.events.models import EngineEvent
from ai_life_engine.exceptions import ConfigurationError, LifeEngineError
from ai_life_engine.heartbeat.models import HeartbeatSnapshot
from ai_life_engine.memory.models import MemoryHit
from ai_life_engine.observability import configure_logging
from ai_life_engine.version import __version__

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_INTERRUPT = 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-life-engine",
        description="Run the AI Life Engine core runtime. No language model is required.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    version = subparsers.add_parser("version", help="Print the package version.")
    version.set_defaults(func=_cmd_version)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-dir",
        type=Path,
        default=Path(".ai-life-engine"),
        help="Directory for the runtime database. Default: ./.ai-life-engine",
    )
    common.add_argument("--name", default=None, help="Runtime display name.")
    common.add_argument("--engine-id", default=None, help="Existing runtime id.")
    common.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )

    init = subparsers.add_parser(
        "init",
        parents=[common],
        help="Create the runtime identity without starting it.",
    )
    init.add_argument("--json", action="store_true", help="Print JSON.")
    init.set_defaults(func=_cmd_init)

    status = subparsers.add_parser(
        "status",
        parents=[common],
        help="Print the persisted runtime status.",
    )
    status.add_argument("--json", action="store_true", help="Print JSON.")
    status.set_defaults(func=_cmd_status)

    health = subparsers.add_parser("health", parents=[common], help="Print a health report.")
    health.add_argument("--json", action="store_true", help="Print JSON.")
    health.add_argument(
        "--expect",
        choices=["healthy", "running"],
        default=None,
        help="Exit 1 unless the report matches this expectation.",
    )
    health.set_defaults(func=_cmd_health)

    run = subparsers.add_parser(
        "run",
        parents=[common],
        help="Start the runtime and wait until it is stopped.",
    )
    run.add_argument(
        "--heartbeat-interval",
        type=float,
        default=None,
        help="Seconds between heartbeats. Omit to wait without ticking.",
    )
    run.set_defaults(func=_cmd_run)

    heartbeat = subparsers.add_parser(
        "heartbeat",
        parents=[common],
        help="Print persisted heartbeat state.",
    )
    heartbeat.add_argument("--json", action="store_true", help="Print JSON.")
    heartbeat.set_defaults(func=_cmd_heartbeat)

    events = subparsers.add_parser(
        "events",
        parents=[common],
        help="Print recent events, newest first.",
    )
    events.add_argument("--json", action="store_true", help="Print JSON.")
    events.add_argument("--limit", type=int, default=20, help="How many events to print.")
    events.set_defaults(func=_cmd_events)

    publish = subparsers.add_parser(
        "publish",
        parents=[common],
        help="Store an event for a runtime.",
    )
    publish.add_argument("--type", required=True, help="Event type, such as ping.")
    publish.add_argument("--source", default="cli", help="Event source name.")
    publish.add_argument(
        "--payload",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="String payload entry. Repeat for more than one.",
    )
    publish.set_defaults(func=_cmd_publish)

    memory = subparsers.add_parser(
        "memory",
        parents=[common],
        help="Print how many memories are stored.",
    )
    memory.add_argument("--json", action="store_true", help="Print JSON.")
    memory.set_defaults(func=_cmd_memory)

    remember = subparsers.add_parser(
        "remember",
        parents=[common],
        help="Store working memory, an episode, or a fact.",
    )
    remember.add_argument(
        "--kind",
        required=True,
        choices=("working", "episode", "fact"),
        help="Which memory store to write.",
    )
    remember.add_argument("--key", default=None, help="Working-memory or fact key.")
    remember.add_argument("--value", default=None, help="Working-memory value.")
    remember.add_argument("--summary", default=None, help="Episode summary.")
    remember.add_argument("--content", default=None, help="Fact content.")
    remember.add_argument("--source", default="cli", help="Episode source name.")
    remember.add_argument(
        "--payload",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="String payload entry. Repeat for more than one.",
    )
    remember.set_defaults(func=_cmd_remember)

    recall = subparsers.add_parser(
        "recall",
        parents=[common],
        help="Search stored memory.",
    )
    recall.add_argument("--query", required=True, help="Text to find.")
    recall.add_argument(
        "--kind",
        default=None,
        choices=("working", "episode", "fact"),
        help="Limit the search to one store.",
    )
    recall.add_argument("--limit", type=int, default=20, help="How many hits to print.")
    recall.add_argument("--json", action="store_true", help="Print JSON.")
    recall.set_defaults(func=_cmd_recall)

    brain = subparsers.add_parser(
        "brain",
        parents=[common],
        help="Print the selected model and usage totals.",
    )
    brain.add_argument("--json", action="store_true", help="Print JSON.")
    brain.set_defaults(func=_cmd_brain)

    complete = subparsers.add_parser(
        "complete",
        parents=[common],
        help="Send one prompt to a model provider.",
    )
    complete.add_argument("--prompt", required=True, help="User prompt.")
    complete.add_argument(
        "--provider",
        default="echo",
        choices=("echo", "openai-compatible"),
        help="Provider name. The default echo provider returns the prompt.",
    )
    complete.add_argument("--base-url", default=None, help="HTTP base URL for openai-compatible.")
    complete.add_argument("--model", default=None, help="Model name for openai-compatible.")
    complete.set_defaults(func=_cmd_complete)

    goals = subparsers.add_parser(
        "goals",
        parents=[common],
        help="List stored goals.",
    )
    goals.add_argument("--json", action="store_true", help="Print JSON.")
    goals.set_defaults(func=_cmd_goals)

    goal_add = subparsers.add_parser(
        "goal-add",
        parents=[common],
        help="Create an active goal.",
    )
    goal_add.add_argument("--title", required=True, help="Short title.")
    goal_add.add_argument("--objective", required=True, help="What the goal is for.")
    goal_add.add_argument(
        "--priority",
        type=int,
        default=3,
        help="1 is considered first. 5 is last.",
    )
    goal_add.set_defaults(func=_cmd_goal_add)

    goal_plan = subparsers.add_parser(
        "goal-plan",
        parents=[common],
        help="Replace a goal's accepted plan. This does not call a model.",
    )
    goal_plan.add_argument("--goal-id", required=True, help="Goal id.")
    goal_plan.add_argument(
        "--step",
        action="append",
        required=True,
        help="Step text. Repeat for more than one step.",
    )
    goal_plan.set_defaults(func=_cmd_goal_plan)

    goal_next = subparsers.add_parser(
        "goal-next",
        parents=[common],
        help="Print the next pending step. It is not executed.",
    )
    goal_next.set_defaults(func=_cmd_goal_next)

    goal_reflect = subparsers.add_parser(
        "goal-reflect",
        parents=[common],
        help="Record an outcome for a goal.",
    )
    goal_reflect.add_argument("--goal-id", required=True, help="Goal id.")
    goal_reflect.add_argument(
        "--outcome",
        required=True,
        choices=("succeeded", "failed", "blocked", "skipped", "noted"),
    )
    goal_reflect.add_argument("--note", required=True, help="What happened.")
    goal_reflect.add_argument("--step-id", default=None, help="Optional step id.")
    goal_reflect.set_defaults(func=_cmd_goal_reflect)

    agents = subparsers.add_parser(
        "agents",
        parents=[common],
        help="Print registered tools, grants, and the selected adapter.",
    )
    agents.add_argument("--json", action="store_true", help="Print JSON.")
    agents.set_defaults(func=_cmd_agents)

    tool_call = subparsers.add_parser(
        "tool-call",
        parents=[common],
        help="Call one tool. It stays denied unless this command grants its permission.",
    )
    tool_call.add_argument("--tool", required=True, help="Tool name.")
    tool_call.add_argument("--argument", required=True, help="Tool argument.")
    tool_call.add_argument(
        "--grant",
        action="append",
        default=[],
        help="Permission to grant for this command only. Repeat to grant more than one.",
    )
    tool_call.set_defaults(func=_cmd_tool_call)

    plugins = subparsers.add_parser(
        "plugins",
        parents=[common],
        help="List plugin setup records. Nothing is loaded unless --load is set.",
    )
    plugins.add_argument("--load", action="store_true", help="Load installed entry points.")
    plugins.add_argument("--json", action="store_true", help="Print JSON.")
    plugins.set_defaults(func=_cmd_plugins)

    serve = subparsers.add_parser(
        "serve",
        parents=[common],
        help="Serve the local HTTP API. The default bind address is loopback.",
    )
    serve.add_argument("--host", default="127.0.0.1", help="Bind address. Default: 127.0.0.1.")
    serve.add_argument(
        "--port", type=int, default=8765, help="TCP port. Default: 8765. 0 picks one."
    )
    serve.add_argument(
        "--grant",
        action="append",
        default=[],
        help="Permission granted to this server process. Repeat to grant more than one.",
    )
    serve.add_argument("--load-plugins", action="store_true", help="Load installed plugins.")
    serve.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow a non-loopback bind address. The API has no authentication.",
    )
    serve.set_defaults(func=_cmd_serve)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command != "version":
            configure_logging(args.log_level)
        return int(args.func(args))
    except (ConfigurationError, LifeEngineError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPT


def _cmd_version(_args: argparse.Namespace) -> int:
    print(__version__)
    return EXIT_OK


def _cmd_init(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        snapshot = engine.initialize()
    finally:
        _close(engine)
    _emit_snapshot(snapshot, as_json=args.json)
    return EXIT_OK


def _cmd_status(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        snapshot = engine.status()
    finally:
        _close(engine)
    _emit_snapshot(snapshot, as_json=args.json)
    return EXIT_OK


def _cmd_health(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        report = engine.health()
    finally:
        _close(engine)
    if args.json:
        print(_dump(report))
    else:
        _print_health(report)
    return _health_exit(report, args.expect)


def _cmd_events(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        rows = engine.events.history(limit=args.limit)
    finally:
        _close(engine)
    if args.json:
        print(json.dumps([row.model_dump(mode="json") for row in rows], indent=2, sort_keys=True))
    else:
        if not rows:
            print("events=0")
            return EXIT_OK
        for row in rows:
            _print_event(row)
    return EXIT_OK


def _cmd_publish(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        event = engine.events.publish(
            args.type,
            source=args.source,
            payload=_payload(args.payload),
        )
    finally:
        _close(engine)
    print(f"event_id={event.event_id}")
    print(f"type={event.type}")
    print(f"status={event.status.value}")
    return EXIT_OK


def _cmd_memory(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        counts = engine.memory.counts()
    finally:
        _close(engine)
    if args.json:
        print(json.dumps(counts.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        print(f"working={counts.working}")
        print(f"episodes={counts.episodes}")
        print(f"facts={counts.facts}")
    return EXIT_OK


def _cmd_remember(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        payload = _payload(args.payload)
        if args.kind == "working":
            if args.key is None or args.value is None:
                raise ConfigurationError("Working memory requires --key and --value.")
            item = engine.memory.remember_working(args.key, args.value, payload=payload)
            print("kind=working")
            print(f"key={item.key}")
        elif args.kind == "episode":
            if args.summary is None:
                raise ConfigurationError("An episode requires --summary.")
            item = engine.memory.remember_episode(
                args.summary,
                source=args.source,
                payload=payload,
            )
            print("kind=episode")
            print(f"episode_id={item.episode_id}")
        else:
            if args.key is None or args.content is None:
                raise ConfigurationError("A fact requires --key and --content.")
            item = engine.memory.remember_fact(args.key, args.content, payload=payload)
            print("kind=fact")
            print(f"key={item.key}")
    finally:
        _close(engine)
    return EXIT_OK


def _cmd_recall(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        hits = engine.memory.search(args.query, kind=args.kind, limit=args.limit)
    finally:
        _close(engine)
    if args.json:
        print(json.dumps([hit.model_dump(mode="json") for hit in hits], indent=2, sort_keys=True))
    else:
        if not hits:
            print("hits=0")
            return EXIT_OK
        for hit in hits:
            _print_hit(hit)
    return EXIT_OK


def _cmd_brain(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        report = _brain_report(engine)
    finally:
        _close(engine)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for key in (
            "configured",
            "provider",
            "model",
            "requests",
            "errors",
            "prompt_tokens",
            "completion_tokens",
        ):
            print(f"{key}={report[key]}")
    return EXIT_OK


def _cmd_complete(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        engine.initialize()
        if args.provider == "echo":
            provider = open_model("echo")
        else:
            if not args.base_url or not args.model:
                raise ConfigurationError("openai-compatible requires --base-url and --model.")
            api_key = os.environ.get("AI_LIFE_ENGINE_API_KEY") or None
            provider = open_model(
                "openai-compatible",
                base_url=args.base_url,
                model=args.model,
                api_key=api_key,
            )
        engine.brain.use(provider)
        response = engine.brain.complete(args.prompt)
    finally:
        _close(engine)
    print(f"provider={response.provider}")
    print(f"model={response.model}")
    print(f"text={response.text}")
    print(f"prompt_tokens={response.usage.prompt_tokens}")
    print(f"completion_tokens={response.usage.completion_tokens}")
    return EXIT_OK


def _brain_report(engine: LifeEngine) -> dict[str, object]:
    totals = engine.brain.totals()
    provider = engine.brain.provider_name or ""
    model = engine.brain.model_name or ""
    return {
        "configured": engine.brain.configured,
        "provider": provider,
        "model": model,
        "requests": totals.requests,
        "errors": totals.errors,
        "prompt_tokens": totals.prompt_tokens,
        "completion_tokens": totals.completion_tokens,
    }


def _cmd_goals(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        rows = engine.goals.list_goals(limit=50)
    finally:
        _close(engine)
    if args.json:
        print(json.dumps([row.model_dump(mode="json") for row in rows], indent=2, sort_keys=True))
    else:
        for row in rows:
            print(f"{row.goal_id} status={row.status.value} priority={row.priority} {row.title}")
    return EXIT_OK


def _cmd_goal_add(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        engine.initialize()
        goal = engine.goals.add(args.title, args.objective, priority=args.priority)
    finally:
        _close(engine)
    print(f"goal_id={goal.goal_id}")
    print(f"status={goal.status.value}")
    print(f"priority={goal.priority}")
    return EXIT_OK


def _cmd_goal_plan(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        plan = engine.goals.plan(UUID(args.goal_id), args.step)
        steps = engine.goals.steps(plan.plan_id)
    finally:
        _close(engine)
    print(f"plan_id={plan.plan_id}")
    for step in steps:
        print(f"step_id={step.step_id} position={step.position} action={step.action}")
    return EXIT_OK


def _cmd_goal_next(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        action = engine.goals.next_action()
    finally:
        _close(engine)
    if action is None:
        print("next=none")
        return EXIT_OK
    print(f"goal_id={action.goal_id}")
    print(f"step_id={action.step_id}")
    print(f"action={action.action}")
    return EXIT_OK


def _cmd_goal_reflect(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        step_id = None if args.step_id is None else UUID(args.step_id)
        reflection = engine.goals.reflect(
            UUID(args.goal_id),
            outcome=args.outcome,
            note=args.note,
            step_id=step_id,
        )
        goal = engine.goals.get(reflection.goal_id)
    finally:
        _close(engine)
    print(f"outcome={reflection.outcome.value}")
    print(f"goal_status={goal.status.value}")
    return EXIT_OK


def _cmd_agents(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        report = {
            "adapter": engine.agents.adapter_name or "",
            "grants": engine.agents.grants(),
            "tools": [tool.model_dump(mode="json") for tool in engine.agents.tools()],
        }
    finally:
        _close(engine)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"adapter={report['adapter']}")
        print(f"grants={','.join(report['grants'])}")
        for tool in report["tools"]:
            print(f"tool={tool['name']} permission={tool['permission']}")
    return EXIT_OK


def _cmd_tool_call(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        engine.initialize()
        for permission in args.grant:
            engine.agents.allow(permission)
        result = engine.agents.call(args.tool, args.argument)
    finally:
        _close(engine)
    print(f"tool={args.tool}")
    print(f"result={result}")
    return EXIT_OK


def _cmd_plugins(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        rows = engine.plugins.load() if args.load else engine.plugins.records()
    finally:
        _close(engine)
    payload = [row.model_dump(mode="json") for row in rows]
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif not payload:
        print("plugins=none")
    else:
        for row in payload:
            print(f"plugin={row['name']} status={row['status']} detail={row['detail']}")
    return EXIT_OK


def _cmd_serve(args: argparse.Namespace) -> int:
    from ai_life_engine.api import create_server, serve_until_stopped

    engine = _open(args)
    try:
        engine.initialize()
        engine.start()
        for permission in args.grant:
            engine.agents.allow(permission)
        if args.load_plugins:
            engine.plugins.load()
        server = create_server(
            engine,
            host=args.host,
            port=args.port,
            allow_remote=args.allow_remote,
        )
        bound_host, bound_port = server.server_address[:2]
        print(f"listening={bound_host}:{bound_port}", flush=True)
        serve_until_stopped(server)
    finally:
        if engine.owns_runtime:
            snapshot = engine.stop(reason="api shutdown")
        else:
            snapshot = engine.status()
        engine.close()
    print(
        "stopped "
        f"engine_id={snapshot.engine_id} "
        f"clean_shutdown={str(snapshot.clean_shutdown).lower()}",
        flush=True,
    )
    return EXIT_OK


def _cmd_heartbeat(args: argparse.Namespace) -> int:
    engine = _open(args)
    try:
        snapshot = engine.heartbeat.status()
    finally:
        _close(engine)
    if args.json:
        print(_dump(snapshot))
    else:
        _print_heartbeat(snapshot)
    return EXIT_OK


def _cmd_run(args: argparse.Namespace) -> int:
    engine = _open(args)
    exit_code = EXIT_OK
    try:

        def _announce(snapshot: RuntimeSnapshot) -> None:
            print(
                f"running engine_id={snapshot.engine_id} pid={snapshot.pid} "
                f"data_dir={snapshot.data_dir}",
                flush=True,
            )

        async def _serve() -> None:
            await engine.run(install_signal_handlers=True, on_started=_announce)

        try:
            asyncio.run(_serve())
        except KeyboardInterrupt:
            exit_code = EXIT_INTERRUPT
            if engine.owns_runtime:
                engine.stop(reason="keyboard interrupt")
        if exit_code == EXIT_OK:
            snapshot = engine.status()
            print(
                "stopped "
                f"engine_id={snapshot.engine_id} "
                f"clean_shutdown={str(snapshot.clean_shutdown).lower()}",
                flush=True,
            )
    finally:
        _close(engine)
    return exit_code


def _open(args: argparse.Namespace) -> LifeEngine:
    kwargs: dict[str, object] = {"data_dir": args.data_dir, "name": args.name}
    if args.engine_id:
        kwargs["engine_id"] = args.engine_id
    interval = getattr(args, "heartbeat_interval", None)
    if interval is not None:
        kwargs["heartbeat_interval"] = interval
    return LifeEngine(EngineConfig.model_validate(kwargs))


def _close(engine: LifeEngine) -> None:
    if engine.owns_runtime:
        engine.stop(reason="cli shutdown")
    engine.close()


def _emit_snapshot(snapshot: RuntimeSnapshot, *, as_json: bool) -> None:
    if as_json:
        print(_dump(snapshot))
        return
    print(f"engine_id={snapshot.engine_id}")
    print(f"name={snapshot.name}")
    print(f"state={snapshot.state.value}")
    persisted = "none" if snapshot.persisted_state is None else snapshot.persisted_state.value
    print(f"persisted_state={persisted}")
    print(f"clean_shutdown={str(snapshot.clean_shutdown).lower()}")
    print(f"recovery_required={str(snapshot.recovery_required).lower()}")
    print(f"blocked_by_live_process={str(snapshot.blocked_by_live_process).lower()}")
    print(f"recovery_count={snapshot.recovery_count}")
    print(f"data_dir={snapshot.data_dir}")


def _payload(items: list[str]) -> dict[str, str]:
    payload: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ConfigurationError("Payload entries must look like key=value.")
        key, value = item.split("=", 1)
        payload[key] = value
    return payload


def _print_hit(hit: MemoryHit) -> None:
    key = "" if hit.key is None else hit.key
    print(f"kind={hit.kind.value}")
    print(f"key={key}")
    print(f"text={hit.text}")


def _print_event(event: EngineEvent) -> None:
    print(f"event_id={event.event_id}")
    print(f"type={event.type}")
    print(f"source={event.source}")
    print(f"status={event.status.value}")
    print(f"payload={json.dumps(event.payload, sort_keys=True)}")


def _print_heartbeat(snapshot: HeartbeatSnapshot) -> None:
    print(f"status={snapshot.status.value}")
    print(f"engine_id={snapshot.engine_id}")
    print(f"tick_count={snapshot.tick_count}")
    print(f"skipped_count={snapshot.skipped_count}")
    print(f"failure_count={snapshot.failure_count}")
    print(f"reasoning_count={snapshot.reasoning_count}")
    print(f"asleep={str(snapshot.asleep).lower()}")
    print(f"recovered={str(snapshot.recovered).lower()}")
    interval = "none" if snapshot.interval_seconds == 0 else f"{snapshot.interval_seconds:.3f}"
    print(f"interval_seconds={interval}")
    print(f"last_error={snapshot.last_error or 'none'}")


def _print_health(report: HealthReport) -> None:
    print(f"status={report.status.value}")
    print(f"engine_id={report.engine_id}")
    print(f"name={report.name}")
    print(f"state={report.state.value}")
    print(f"owns_runtime={str(report.owns_runtime).lower()}")
    print(f"recovery_required={str(report.recovery_required).lower()}")
    uptime = "none" if report.uptime_seconds is None else f"{report.uptime_seconds:.3f}"
    print(f"uptime_seconds={uptime}")
    for check in report.checks:
        print(f"check {check.name} {check.status.value} {check.message}")


def _health_exit(report: HealthReport, expect: str | None) -> int:
    if expect == "healthy":
        return EXIT_OK if report.status is HealthStatus.HEALTHY else EXIT_FAILURE
    if expect == "running":
        running = report.status is HealthStatus.HEALTHY and report.state is LifecycleState.RUNNING
        return EXIT_OK if running else EXIT_FAILURE
    if report.status in {HealthStatus.HEALTHY, HealthStatus.INACTIVE}:
        return EXIT_OK
    return EXIT_FAILURE


def _dump(model: RuntimeSnapshot | HealthReport | HeartbeatSnapshot) -> str:
    return json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True)
