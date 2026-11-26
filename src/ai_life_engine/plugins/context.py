# SPDX-License-Identifier: Apache-2.0
"""The only runtime surface a plugin receives during setup."""

from __future__ import annotations


class PluginContext:
    """Register extensions. This object cannot grant permissions or call tools."""

    def __init__(self, engine: object) -> None:
        self._engine = engine

    def register_tool(self, tool: object) -> None:
        """Add a tool. It stays denied until the application grants it."""

        self._engine.agents.register(tool)

    def register_health_check(self, check: object) -> None:
        """Add a health check with a non-reserved name."""

        self._engine.register_health_check(check)

    def subscribe(self, event_type: str, handler: object) -> str:
        """Register an event handler. Returns the subscription id."""

        return self._engine.events.subscribe(event_type, handler)
