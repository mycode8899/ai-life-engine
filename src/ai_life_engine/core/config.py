# SPDX-License-Identifier: Apache-2.0
"""Configuration for one runtime data directory."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_life_engine.exceptions import ConfigurationError
from ai_life_engine.validation import clean_metadata, clean_name


class EngineConfig(BaseModel):
    """Where a runtime lives and how it should identify itself.

    ``name`` and ``metadata`` are optional. When they are omitted, a start
    keeps the values already stored. When they are set, the process that
    claims the runtime writes them. Metadata is not a place for API keys.

    ``heartbeat_interval`` is seconds between ticks inside ``run``. ``None``
    leaves ``run`` waiting without ticking. The default reasoning policy
    still does not call a model.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str | None = None
    engine_id: UUID | None = None
    data_dir: Path = Field(default_factory=lambda: Path(".ai-life-engine"))
    database_filename: str = "engine.sqlite3"
    metadata: dict[str, str] | None = None
    heartbeat_interval: float | None = None
    heartbeat_max_failures: int = 5
    event_poll_seconds: float = 0.25
    event_handler_timeout: float = 1.0
    model_timeout: float = 30.0

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return clean_name(value)

    @field_validator("database_filename")
    @classmethod
    def _database_filename(cls, value: str) -> str:
        if not isinstance(value, str) or value != Path(value).name or value in {"", ".", ".."}:
            raise ValueError("database_filename must be a file name, not a path.")
        if len(value) > 128 or any(ord(character) < 32 for character in value):
            raise ValueError("database_filename must be 1..128 characters without controls.")
        return value

    @field_validator("metadata")
    @classmethod
    def _metadata(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        return clean_metadata(value)

    @field_validator("heartbeat_interval", mode="before")
    @classmethod
    def _heartbeat_interval(cls, value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("heartbeat_interval must be a number of seconds.")
        number = float(value)
        if number < 0.01 or number > 86400:
            raise ValueError("heartbeat_interval must be between 0.01 and 86400 seconds.")
        return number

    @field_validator("event_poll_seconds", mode="before")
    @classmethod
    def _event_poll(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("event_poll_seconds must be a number of seconds.")
        number = float(value)
        if number < 0.05 or number > 60:
            raise ValueError("event_poll_seconds must be between 0.05 and 60 seconds.")
        return number

    @field_validator("event_handler_timeout", mode="before")
    @classmethod
    def _event_handler_timeout(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("event_handler_timeout must be a number of seconds.")
        number = float(value)
        if number < 0.05 or number > 30:
            raise ValueError("event_handler_timeout must be between 0.05 and 30 seconds.")
        return number

    @field_validator("model_timeout", mode="before")
    @classmethod
    def _model_timeout(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("model_timeout must be a number of seconds.")
        number = float(value)
        if number < 0.05 or number > 120:
            raise ValueError("model_timeout must be between 0.05 and 120 seconds.")
        return number

    @field_validator("heartbeat_max_failures", mode="before")
    @classmethod
    def _heartbeat_max_failures(cls, value: object) -> int:
        if type(value) is not int or value < 1 or value > 100:
            raise ValueError("heartbeat_max_failures must be an integer from 1 to 100.")
        return value

    def resolved_data_dir(self) -> Path:
        """Return the absolute data directory, refusing the filesystem root."""

        path = Path(self.data_dir).expanduser().resolve(strict=False)
        if path.parent == path:
            raise ConfigurationError("Refusing to use the filesystem root as the data directory.")
        return path

    def database_path(self) -> Path:
        return self.resolved_data_dir() / self.database_filename
