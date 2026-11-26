# SPDX-License-Identifier: Apache-2.0
"""Plugin loader.

Entry points in the group ``ai_life_engine.plugins`` are loaded only when the
application calls ``load``. Setup can register tools, health checks, and
event handlers. It cannot grant a permission.
"""

from ai_life_engine.plugins.context import PluginContext
from ai_life_engine.plugins.health import plugins_check
from ai_life_engine.plugins.models import PluginRecord, PluginStatus
from ai_life_engine.plugins.service import Plugins

__all__ = [
    "PluginContext",
    "PluginRecord",
    "PluginStatus",
    "Plugins",
    "plugins_check",
]
