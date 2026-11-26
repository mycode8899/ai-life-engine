# SPDX-License-Identifier: Apache-2.0
"""Small HTTP API on the standard library.

The default bind address is the loopback interface. There is no
authentication. A non-loopback address has to be requested explicitly.
"""

from __future__ import annotations

import json
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from ai_life_engine.exceptions import AgentError, ConfigurationError, LifeEngineError

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_MAX_BODY = 32_768


def create_server(
    engine: object,
    *,
    host: str,
    port: int,
    allow_remote: bool = False,
) -> ThreadingHTTPServer:
    """Bind an HTTP server. This does not serve requests yet."""

    _check_bind(host, port, allow_remote=allow_remote)
    handler = _handler_type(engine)
    try:
        return ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        raise ConfigurationError(f"Cannot bind {host}:{port}.") from exc


def serve_until_stopped(server: ThreadingHTTPServer, *, install_signals: bool = True) -> None:
    """Serve until interrupt. Signal handlers are installed only when asked."""

    if install_signals:
        _install_stop(server)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _check_bind(host: object, port: object, *, allow_remote: bool) -> None:
    if not isinstance(host, str) or not host or len(host) > 255:
        raise ConfigurationError("host must be a bind address.")
    if host not in _LOOPBACK and not allow_remote:
        raise ConfigurationError("Refusing to listen beyond loopback without allow_remote.")
    if isinstance(port, bool) or not isinstance(port, int) or port < 0 or port > 65535:
        raise ConfigurationError("port must be an integer from 0 to 65535.")


def _install_stop(server: ThreadingHTTPServer) -> None:
    def handle(signum: int, frame: object) -> None:
        del signum, frame
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, handle)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle)


def _handler_type(engine: object) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def log_message(self, fmt: str, *args: object) -> None:
            del fmt, args

        def _dispatch(self, method: str) -> None:
            path = urlparse(self.path).path.rstrip("/") or "/"
            try:
                status, body = _route(engine, method, path, self._body())
            except LifeEngineError as exc:
                status = _error_status(exc)
                body = {"error": str(exc), "code": exc.code}
            self._send(status, body)

        def _body(self) -> object:
            if self.command != "POST":
                return None
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ConfigurationError("Content-Length must be an integer.") from exc
            if length < 0 or length > _MAX_BODY:
                raise ConfigurationError("Request body must be at most 32768 bytes.")
            raw = self.rfile.read(length)
            if not raw:
                return {}
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ConfigurationError("Request body must be JSON.") from exc

        def _send(self, status: int, body: object) -> None:
            raw = json.dumps(body, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return Handler


def _route(engine: object, method: str, path: str, body: object) -> tuple[int, object]:
    if method == "GET" and path == "/v1/health":
        return 200, engine.health().model_dump(mode="json")
    if method == "GET" and path == "/v1/status":
        return 200, engine.status().model_dump(mode="json")
    if method == "GET" and path == "/v1/goals":
        rows = engine.goals.list_goals(limit=50)
        return 200, [row.model_dump(mode="json") for row in rows]
    if method == "GET" and path == "/v1/next-action":
        action = engine.goals.next_action()
        if action is None:
            return 200, {"action": None}
        return 200, action.model_dump(mode="json")
    if method == "GET" and path == "/v1/tools":
        rows = engine.agents.tools()
        return 200, [row.model_dump(mode="json") for row in rows]
    if method == "GET" and path == "/v1/plugins":
        rows = engine.plugins.records()
        return 200, [row.model_dump(mode="json") for row in rows]
    if method == "POST" and path == "/v1/tools/call":
        payload = _object(body)
        result = engine.agents.call(_text(payload, "name"), _text(payload, "argument"))
        return 200, {"result": result}
    if method == "POST" and path == "/v1/events":
        payload = _object(body)
        event_type = _text(payload, "type")
        extra = payload.get("payload", {})
        if extra is None:
            extra = {}
        if not isinstance(extra, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in extra.items()
        ):
            raise ConfigurationError("Event payload must be a string map.")
        event = engine.events.publish(event_type, source="api", payload=extra)
        return 200, event.model_dump(mode="json")
    if method not in {"GET", "POST"}:
        raise ConfigurationError("Method is not supported.")
    raise LifeEngineError("Unknown path.", code="not_found")


def _object(body: object) -> dict[str, object]:
    if not isinstance(body, dict):
        raise ConfigurationError("Request body must be a JSON object.")
    return body


def _text(body: dict[str, object], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise ConfigurationError(f"{key} must be a string.")
    return value


def _error_status(exc: LifeEngineError) -> int:
    if isinstance(exc, AgentError) and exc.code == "denied":
        return 403
    if exc.code in {"not_running", "still_active"}:
        return 409
    if exc.code == "not_found":
        return 404
    return 400
