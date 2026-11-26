# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_life_engine import ConfigurationError, EngineConfig


def test_defaults_are_local_and_optional() -> None:
    config = EngineConfig()
    assert config.name is None
    assert config.engine_id is None
    assert config.metadata is None
    assert config.database_filename == "engine.sqlite3"
    assert config.resolved_data_dir().name == ".ai-life-engine"


def test_name_metadata_and_unicode_are_preserved(tmp_path: Path) -> None:
    config = EngineConfig(
        data_dir=tmp_path / "data",
        name="生命周期",
        metadata={"note": 'it is a "quote"'},
    )
    assert config.name == "生命周期"
    assert config.metadata == {"note": 'it is a "quote"'}


def test_rejects_secrets_paths_and_control_characters(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        EngineConfig(metadata={"api_key": "nope"})
    with pytest.raises(ValidationError):
        EngineConfig(metadata={"openai_api_key": "nope"})
    with pytest.raises(ValidationError):
        EngineConfig(name="bad\nname")
    with pytest.raises(ValidationError):
        EngineConfig(database_filename="../engine.sqlite3")
    with pytest.raises(ValidationError):
        EngineConfig(data_dir=tmp_path, metadata={"ok": "x" * 257})


def test_refuses_filesystem_root() -> None:
    config = EngineConfig(data_dir=Path(Path.cwd().anchor))
    with pytest.raises(ConfigurationError):
        config.resolved_data_dir()


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        EngineConfig(model="gpt")  # type: ignore[call-arg]
