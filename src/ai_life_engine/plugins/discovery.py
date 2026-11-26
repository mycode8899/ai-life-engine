# SPDX-License-Identifier: Apache-2.0
"""Find installed plugins.

The entry-point group is ``ai_life_engine.plugins``. Importing this module
does not load plugins.
"""

from __future__ import annotations

from importlib.metadata import EntryPoints, entry_points


def discover_plugins() -> EntryPoints:
    """Return installed entry points. This does not call ``load``."""

    return entry_points(group="ai_life_engine.plugins")
