# SPDX-License-Identifier: Apache-2.0
"""Logging helpers.

The library does not attach a handler unless the application asks. The CLI
calls :func:`configure_logging`.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

LOGGER_NAME = "ai_life_engine"


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(name)


def configure_logging(level: str = "INFO", *, stream: TextIO | None = None) -> None:
    """Attach one stderr handler to the ``ai_life_engine`` logger.

    If a non-null handler is already installed, only the level is updated.
    """

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    if any(not isinstance(handler, logging.NullHandler) for handler in logger.handlers):
        return
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
