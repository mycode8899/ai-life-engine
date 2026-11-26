# SPDX-License-Identifier: Apache-2.0
"""Records of plugin setup.

A plugin is code that registers tools or checks. Loading it does not grant
permissions and does not start the runtime.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class PluginStatus(StrEnum):
    """Whether setup finished."""

    LOADED = "loaded"
    FAILED = "failed"


class PluginRecord(BaseModel):
    """One setup attempt. This record is not written to the database."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    status: PluginStatus
    detail: str
