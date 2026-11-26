# SPDX-License-Identifier: Apache-2.0
"""Tools, permissions, and agent adapters.

Tools are registered and denied until this process grants their permission.
An adapter chooses a tool. The runtime runs it. No framework is imported.
"""

from ai_life_engine.agents.adapters import EchoAgent, ScriptedAgent
from ai_life_engine.agents.health import agents_check
from ai_life_engine.agents.models import AgentStep, ToolCallRecord, ToolInfo
from ai_life_engine.agents.service import Agents
from ai_life_engine.agents.tools import EchoTool, FinishTool, NoteTool

__all__ = [
    "AgentStep",
    "Agents",
    "EchoAgent",
    "EchoTool",
    "FinishTool",
    "NoteTool",
    "ScriptedAgent",
    "ToolCallRecord",
    "ToolInfo",
    "agents_check",
]
