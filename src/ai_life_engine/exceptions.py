# SPDX-License-Identifier: Apache-2.0
"""Public exception types for AI Life Engine."""

from __future__ import annotations

from ai_life_engine.core.state import LifecycleState


class LifeEngineError(Exception):
    """Base error for every failure raised by the public runtime API."""

    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


class ConfigurationError(LifeEngineError):
    """The engine was constructed or configured in a way that cannot run."""

    def __init__(self, message: str, *, code: str = "configuration") -> None:
        super().__init__(message, code=code)


class LifecycleError(LifeEngineError):
    """A lifecycle call is not valid for the current runtime state."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "lifecycle",
        state: LifecycleState | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.state = state


class PersistenceError(LifeEngineError):
    """Durable state could not be read or written."""

    def __init__(self, message: str, *, code: str = "persistence") -> None:
        super().__init__(message, code=code)


class AgentError(LifeEngineError):
    """A tool call or agent step could not be completed.

    ``code`` is ``not_configured``, ``not_found``, ``denied``, ``rejected``,
    ``unavailable``, ``invalid_argument``, or ``conflict``. The runtime stays up.
    """

    def __init__(self, message: str, *, code: str = "agent") -> None:
        super().__init__(message, code=code)


class GoalError(LifeEngineError):
    """A goal, plan, or reflection could not be changed.

    ``code`` is ``not_found``, ``invalid_argument``, ``full``, or ``conflict``.
    The runtime stays up.
    """

    def __init__(self, message: str, *, code: str = "goal") -> None:
        super().__init__(message, code=code)


class ModelError(LifeEngineError):
    """A model call could not be completed.

    ``code`` is ``not_configured``, ``timeout``, ``unavailable``, ``rejected``,
    or ``invalid_argument``. The runtime stays up.
    """

    def __init__(self, message: str, *, code: str = "model") -> None:
        super().__init__(message, code=code)
