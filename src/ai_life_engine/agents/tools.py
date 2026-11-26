# SPDX-License-Identifier: Apache-2.0
"""Built-in tools.

These tools do not open a shell, read the filesystem, or call the network.
Each one declares the permission a caller must grant before it will run.
"""

from __future__ import annotations

from collections.abc import Callable


class EchoTool:
    """Return the argument unchanged."""

    name = "echo"
    description = "Return the argument unchanged."
    permission = "echo"

    def call(self, argument: str) -> str:
        return argument


class NoteTool:
    """Append the argument as an episode."""

    name = "note"
    description = "Store the argument as an episode."
    permission = "memory.write"

    def __init__(self, write: Callable[[str], None]) -> None:
        self._write = write

    def call(self, argument: str) -> str:
        self._write(argument)
        return "Noted."


class FinishTool:
    """Mark the current next action succeeded."""

    name = "finish"
    description = "Record the current next action as succeeded."
    permission = "goals.write"

    def __init__(self, finish: Callable[[str], str]) -> None:
        self._finish = finish

    def call(self, argument: str) -> str:
        action = self._finish(argument)
        return f"Completed {action}."
