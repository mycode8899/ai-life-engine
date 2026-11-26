# SPDX-License-Identifier: Apache-2.0
"""Agent adapters.

An adapter chooses a tool. It does not run the tool and it does not bypass
the permission check. These adapters do not import an agent framework.
"""

from __future__ import annotations

import threading

from ai_life_engine.agents.models import AgentDecision, AgentRequest
from ai_life_engine.exceptions import ConfigurationError


class EchoAgent:
    """Choose the echo tool and pass it the current next action."""

    name = "echo-agent"

    def decide(self, request: AgentRequest) -> AgentDecision | None:
        if request.next_action is None or "echo" not in request.tools:
            return None
        return AgentDecision(tool="echo", argument=request.next_action)


class ScriptedAgent:
    """Return a fixed list of tool choices, one per step."""

    name = "scripted"

    def __init__(self, decisions: list[tuple[str, str]]) -> None:
        if not isinstance(decisions, list):
            raise ConfigurationError("Scripted decisions must be a list.")
        checked: list[tuple[str, str]] = []
        for item in decisions:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], str)
                or not isinstance(item[1], str)
            ):
                raise ConfigurationError("Each scripted decision must be a (tool, argument) pair.")
            checked.append(item)
        self._decisions = checked
        self._lock = threading.Lock()

    def decide(self, request: AgentRequest) -> AgentDecision | None:
        del request
        with self._lock:
            if not self._decisions:
                return None
            tool, argument = self._decisions.pop(0)
        return AgentDecision(tool=tool, argument=argument)
