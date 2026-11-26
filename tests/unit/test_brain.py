# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ai_life_engine import (
    EchoProvider,
    EngineConfig,
    LifecycleError,
    LifeEngine,
    ModelError,
    ReasoningDecision,
    open_model,
)
from ai_life_engine.brain.providers import HttpChatProvider, ScriptedProvider
from ai_life_engine.core.health import HealthStatus
from ai_life_engine.heartbeat.models import WakeReason
from tests.support.scripted import ScriptedScheduler


class _Ask:
    def decide(self, observation: object) -> ReasoningDecision:
        del observation
        return ReasoningDecision(required=True, reason="Ask the selected model.")


def test_complete_requires_a_provider_and_an_identity(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "fresh", model_timeout=1))
    try:
        assert engine.brain.configured is False
        assert engine.capabilities["llm"] is True
        with pytest.raises(LifecycleError):
            engine.brain.complete("hello")
        engine.initialize()
        with pytest.raises(ModelError) as caught:
            engine.brain.complete("hello")
        assert caught.value.code == "not_configured"
    finally:
        engine.close()


def test_providers_can_be_switched_without_a_new_runtime(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "switch", name="switch"))
    try:
        engine.initialize()
        engine.brain.use(open_model("echo"))
        assert engine.brain.complete("hello").text == "hello"
        engine.brain.use(ScriptedProvider(["from-script"]))
        assert engine.brain.complete("hello").text == "from-script"
        rows = engine.brain.usage(limit=5)
        assert [row.provider for row in rows] == ["scripted", "echo"]
        assert engine.brain.totals().requests == 2
    finally:
        engine.close()

    follow = LifeEngine(EngineConfig(data_dir=tmp_path / "switch"))
    try:
        assert follow.brain.configured is False
        assert follow.brain.totals().requests == 2
        assert follow.brain.usage(limit=1)[0].provider == "scripted"
    finally:
        follow.close()


def test_http_provider_and_a_failed_call(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            if body["model"] == "fail":
                self.send_response(503)
                self.end_headers()
                return
            raw = json.dumps(
                {
                    "choices": [{"message": {"content": "from-http"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt: str, *args: object) -> None:
            del fmt, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}/v1"
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "http", name="http", model_timeout=2))
    try:
        engine.initialize()
        engine.brain.use(EchoProvider())
        assert engine.brain.complete("local").text == "local"
        engine.brain.use(HttpChatProvider(base_url=base_url, model="demo"))
        response = engine.brain.complete("remote", temperature=0, max_tokens=16)
        assert response.text == "from-http"
        assert response.provider == "openai-compatible"
        assert response.usage.prompt_tokens == 3
        engine.brain.use(HttpChatProvider(base_url=base_url, model="fail"))
        with pytest.raises(ModelError) as caught:
            engine.brain.complete("remote")
        assert caught.value.code == "unavailable"
        engine.start()
        report = engine.health()
        assert report.status is HealthStatus.DEGRADED
        brain = next(check for check in report.checks if check.name == "brain")
        assert brain.status.value == "warn"
        engine.stop()
    finally:
        engine.close()
        server.shutdown()


def test_default_policy_does_not_call_a_selected_model(tmp_path: Path) -> None:
    config = EngineConfig(data_dir=tmp_path / "quiet", heartbeat_interval=1)
    scheduler = ScriptedScheduler([WakeReason.INTERVAL, WakeReason.STOP])
    engine = LifeEngine(config, scheduler=scheduler)
    engine.brain.use(EchoProvider())
    try:
        asyncio.run(engine.run())
        assert engine.heartbeat.status().tick_count == 1
        assert engine.heartbeat.status().reasoning_count == 0
        assert engine.brain.totals().requests == 0
    finally:
        engine.close()


def test_required_policy_uses_the_selected_model(tmp_path: Path) -> None:
    config = EngineConfig(data_dir=tmp_path / "ask", heartbeat_interval=1)
    scheduler = ScriptedScheduler([WakeReason.INTERVAL, WakeReason.STOP])
    engine = LifeEngine(config, scheduler=scheduler, reasoning_policy=_Ask())
    engine.brain.use(ScriptedProvider(["noted"]))
    try:
        asyncio.run(engine.run())
        status = engine.heartbeat.status()
        assert status.tick_count == 1
        assert status.reasoning_count == 1
        assert engine.brain.totals().requests == 1
        assert engine.brain.usage(limit=1)[0].provider == "scripted"
    finally:
        engine.close()
