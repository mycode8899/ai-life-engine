# SPDX-License-Identifier: Apache-2.0
"""HTTP API for one local runtime.

The server uses the Python standard library. It does not grant tools and it
does not load plugins unless the caller asks.
"""

from ai_life_engine.api.server import create_server, serve_until_stopped

__all__ = ["create_server", "serve_until_stopped"]
