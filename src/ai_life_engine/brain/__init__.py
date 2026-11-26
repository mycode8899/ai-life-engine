# SPDX-License-Identifier: Apache-2.0
"""Model interface for one runtime.

Selecting a provider does not change lifecycle, heartbeat, or memory code.
No provider is selected until the application calls ``use``.
"""

from ai_life_engine.brain.health import brain_check
from ai_life_engine.brain.models import (
    ModelRequest,
    ModelResponse,
    TokenUsage,
    UsageRecord,
    UsageStatus,
    UsageTotals,
)
from ai_life_engine.brain.providers import (
    EchoProvider,
    HttpChatProvider,
    ScriptedProvider,
    open_model,
)
from ai_life_engine.brain.service import Brain

__all__ = [
    "Brain",
    "EchoProvider",
    "HttpChatProvider",
    "ModelRequest",
    "ModelResponse",
    "ScriptedProvider",
    "TokenUsage",
    "UsageRecord",
    "UsageStatus",
    "UsageTotals",
    "brain_check",
    "open_model",
]
