# SPDX-License-Identifier: Apache-2.0
"""Tool registry, permissions, and one agent step.

Nothing is granted at startup. ``call`` and ``step`` run a tool only after
``allow`` has granted its permission. The heartbeat does not call either one.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Protocol
from uuid import UUID, uuid4

from ai_life_engine.agents.models import (
    AgentDecision,
    AgentRequest,
    AgentStep,
    ToolCallRecord,
    ToolCallStatus,
    ToolDecision,
    ToolInfo,
)
from ai_life_engine.agents.store import supports_agents
from ai_life_engine.agents.tools import EchoTool, FinishTool, NoteTool
from ai_life_engine.core.time import utcnow
from ai_life_engine.exceptions import (
    AgentError,
    ConfigurationError,
    LifecycleError,
    PersistenceError,
)
from ai_life_engine.observability import get_logger
from ai_life_engine.validation import bounded_limit, clean_reason

logger = get_logger("ai_life_engine.agents")

_NAME = re.compile(r"[a-z][a-z0-9._-]{0,63}")


class AgentHost(Protocol):
    """Narrow view of the runtime the agent facade is allowed to touch."""

    @property
    def lock(self) -> object: ...

    @property
    def store(self) -> object: ...

    def engine_id(self) -> UUID: ...

    def ensure_open(self) -> None: ...

    def refresh(self) -> None: ...

    def has_identity(self) -> bool: ...


class Agents:
    """Register tools, grant permissions, and ask an adapter for one step."""

    def __init__(
        self,
        host: AgentHost,
        *,
        note: Callable[[str], None],
        finish: Callable[[str], str],
        next_action: Callable[[], str | None],
    ) -> None:
        self._host = host
        self._tools: dict[str, object] = {}
        self._grants: set[str] = set()
        self._adapter: object | None = None
        self.register(EchoTool())
        self.register(NoteTool(note))
        self.register(FinishTool(finish))
        self._next_action = next_action

    @property
    def adapter_name(self) -> str | None:
        with self._host.lock:
            self._host.ensure_open()
            if self._adapter is None:
                return None
            return str(getattr(self._adapter, "name", "")) or None

    def allow(self, permission: str) -> None:
        """Grant a permission for this process. Grants are not stored."""

        name = _permission(permission)
        with self._host.lock:
            self._host.ensure_open()
            self._grants.add(name)
        logger.info("permission granted permission=%s", name)

    def revoke(self, permission: str) -> None:
        """Remove a permission from this process."""

        name = _permission(permission)
        with self._host.lock:
            self._host.ensure_open()
            self._grants.discard(name)
        logger.info("permission revoked permission=%s", name)

    def grants(self) -> list[str]:
        """Return granted permissions, sorted."""

        with self._host.lock:
            self._host.ensure_open()
            return sorted(self._grants)

    def register(self, tool: object) -> None:
        """Add a tool. The name must be unique. Registration does not grant it."""

        info = _require_tool(tool)
        with self._host.lock:
            self._host.ensure_open()
            if info.name in self._tools:
                raise AgentError(f"Tool {info.name} is already registered.", code="conflict")
            self._tools[info.name] = tool
        logger.info("tool registered name=%s permission=%s", info.name, info.permission)

    def tools(self) -> list[ToolInfo]:
        """Return registered tools, sorted by name."""

        with self._host.lock:
            self._host.ensure_open()
            rows = [_require_tool(tool) for tool in self._tools.values()]
        return sorted(rows, key=lambda item: item.name)

    def use(self, adapter: object | None) -> None:
        """Select an agent adapter, or clear it when ``adapter`` is None."""

        if adapter is not None:
            _require_adapter(adapter)
        with self._host.lock:
            self._host.ensure_open()
            self._adapter = adapter
        if adapter is None:
            logger.info("agent adapter cleared")
        else:
            logger.info("agent adapter selected name=%s", adapter.name)

    def call(self, name: str, argument: str, *, agent: str = "direct") -> str:
        """Run one tool when its permission is granted."""

        tool_name = _label(name, "Tool name")
        cleaned = _line(argument, "Argument", 500)
        caller = _label(agent, "Agent name")
        with self._host.lock:
            self._ready()
            tool = self._tools.get(tool_name)
            if tool is None:
                raise AgentError(f"No tool is named {tool_name}.", code="not_found")
            info = _require_tool(tool)
            granted = info.permission in self._grants
        if not granted:
            self._record(
                agent=caller,
                tool=info.name,
                permission=info.permission,
                decision=ToolDecision.DENIED,
                status=ToolCallStatus.DENIED,
                detail="Permission denied.",
            )
            raise AgentError(
                f"Permission {info.permission} is not granted.",
                code="denied",
            )
        try:
            produced = tool.call(cleaned)
        except AgentError as exc:
            self._record(
                agent=caller,
                tool=info.name,
                permission=info.permission,
                decision=ToolDecision.ALLOWED,
                status=ToolCallStatus.ERROR,
                detail=str(exc),
            )
            raise
        except Exception as exc:
            error = AgentError("The tool failed.", code="unavailable")
            self._record(
                agent=caller,
                tool=info.name,
                permission=info.permission,
                decision=ToolDecision.ALLOWED,
                status=ToolCallStatus.ERROR,
                detail=str(error),
            )
            raise error from exc
        result = _line(produced, "Tool result", 500)
        self._record(
            agent=caller,
            tool=info.name,
            permission=info.permission,
            decision=ToolDecision.ALLOWED,
            status=ToolCallStatus.OK,
            detail=result,
        )
        logger.info("tool completed name=%s agent=%s", info.name, caller)
        return result

    def step(self) -> AgentStep:
        """Ask the selected adapter for one tool call.

        The adapter sees the next action and the tools that are both
        registered and granted. A missing adapter raises. A decision of None
        does not call a tool.
        """

        with self._host.lock:
            self._ready()
            adapter = self._adapter
            if adapter is None:
                raise AgentError("No agent adapter is selected.", code="not_configured")
            available = sorted(
                name
                for name, tool in self._tools.items()
                if _require_tool(tool).permission in self._grants
            )
            request = AgentRequest(next_action=self._next_action(), tools=available)
            adapter_name = str(adapter.name)
        try:
            decision = adapter.decide(request)
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError("The agent adapter failed.", code="unavailable") from exc
        if decision is None:
            return AgentStep(
                executed=False,
                detail="The agent did not choose a tool.",
            )
        if not isinstance(decision, AgentDecision):
            raise AgentError(
                "The agent adapter returned an unexpected decision.",
                code="rejected",
            )
        result = self.call(decision.tool, decision.argument, agent=adapter_name)
        return AgentStep(
            executed=True,
            tool=decision.tool,
            result=result,
            detail="Tool call completed.",
        )

    def calls(self, *, limit: int = 20) -> list[ToolCallRecord]:
        """Return recent tool calls, newest first."""

        bounded = _limit(limit)
        with self._host.lock:
            store = self._ready()
            return store.list_tool_calls(self._host.engine_id(), limit=bounded)

    def latest_for_health(self) -> ToolCallRecord | None:
        """Return the newest tool call, or None when storage cannot answer."""

        try:
            self._host.ensure_open()
            store = self._host.store
            if not supports_agents(store):
                return None
            with self._host.lock:
                if not self._host.has_identity():
                    return None
                return store.latest_tool_call(self._host.engine_id())
        except PersistenceError:
            return None

    def _ready(self) -> object:
        self._host.ensure_open()
        self._host.refresh()
        if not self._host.has_identity():
            raise LifecycleError(
                "Initialize or start the runtime before calling a tool.",
                code="not_running",
            )
        store = self._host.store
        if not supports_agents(store):
            raise ConfigurationError("This state store does not persist tool calls.")
        return store

    def _record(
        self,
        *,
        agent: str,
        tool: str,
        permission: str,
        decision: ToolDecision,
        status: ToolCallStatus,
        detail: str,
    ) -> None:
        cleaned = clean_reason(detail, limit=200)
        with self._host.lock:
            store = self._ready()
            store.append_tool_call(
                call_id=uuid4(),
                engine_id=self._host.engine_id(),
                agent=agent,
                tool=tool,
                permission=permission,
                decision=decision,
                status=status,
                detail=cleaned,
                created_at=utcnow(),
            )


def _require_tool(tool: object) -> ToolInfo:
    name = getattr(tool, "name", None)
    description = getattr(tool, "description", None)
    permission = getattr(tool, "permission", None)
    call = getattr(tool, "call", None)
    if (
        not isinstance(name, str)
        or not isinstance(description, str)
        or not isinstance(permission, str)
        or not callable(call)
    ):
        raise ConfigurationError("A tool needs name, description, permission, and call().")
    return ToolInfo(
        name=_label(name, "Tool name"),
        description=_line(description, "Tool description", 200),
        permission=_permission(permission),
    )


def _require_adapter(adapter: object) -> None:
    name = getattr(adapter, "name", None)
    decide = getattr(adapter, "decide", None)
    if not isinstance(name, str) or not callable(decide) or not _NAME.fullmatch(name):
        raise ConfigurationError("An agent adapter needs a name and decide().")


def _label(value: object, label: str) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise AgentError(
            f"{label} must be a lowercase name of 1..64 characters.",
            code="invalid_argument",
        )
    return value


def _permission(value: object) -> str:
    return _label(value, "Permission")


def _line(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise AgentError(f"{label} must be text.", code="invalid_argument")
    try:
        return clean_reason(value, limit=limit)
    except ValueError as exc:
        raise AgentError(
            f"{label} must be 1..{limit} characters.",
            code="invalid_argument",
        ) from exc


def _limit(limit: int) -> int:
    try:
        return bounded_limit(limit)
    except ValueError as exc:
        raise AgentError(str(exc), code="invalid_argument") from exc
