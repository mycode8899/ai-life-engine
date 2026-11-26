# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ai_life_engine import EngineConfig, LifeEngine
from ai_life_engine.api import create_server
from ai_life_engine.exceptions import ConfigurationError


def _serve(engine: LifeEngine):
    server = create_server(engine, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _request(url: str, payload: dict[str, object] | None = None) -> tuple[int, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="GET" if data is None else "POST")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_local_api_reads_state_and_refuses_an_ungranted_tool(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "api", name="api"))
    server = None
    thread = None
    try:
        assert engine.capabilities["rest_api"] is True
        engine.initialize()
        engine.start()
        engine.agents.allow("echo")
        server, thread = _serve(engine)
        host, port = server.server_address[:2]
        base = f"http://{host}:{port}"
        status, health = _request(f"{base}/v1/health")
        assert status == 200
        assert health["status"] == "healthy"
        status, tools = _request(f"{base}/v1/tools")
        assert status == 200
        assert {row["name"] for row in tools} == {"echo", "finish", "note"}
        status, body = _request(f"{base}/v1/tools/call", {"name": "echo", "argument": "hello"})
        assert status == 200
        assert body == {"result": "hello"}
        status, denied = _request(f"{base}/v1/tools/call", {"name": "note", "argument": "secret"})
        assert status == 403
        assert denied["code"] == "denied"
        status, missing = _request(f"{base}/v1/missing")
        assert status == 404
        status, event = _request(f"{base}/v1/events", {"type": "note", "payload": {"text": "hi"}})
        assert status == 200
        assert event["type"] == "note"
        assert event["source"] == "api"
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        if engine.owns_runtime:
            engine.stop()
        engine.close()


def test_remote_bind_requires_an_explicit_flag(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "remote", name="remote"))
    try:
        with pytest.raises(ConfigurationError):
            create_server(engine, host="0.0.0.0", port=9)
    finally:
        engine.close()
