# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from ai_life_engine import EngineConfig, LifeEngine
from ai_life_engine.core.health import CheckStatus, HealthStatus
from ai_life_engine.plugins.models import PluginStatus


class _Ping:
    name = "ping"
    description = "Return pong."
    permission = "ping"

    def call(self, argument: str) -> str:
        del argument
        return "pong"


class _Good:
    name = "sample"

    def setup(self, context: object) -> None:
        context.register_tool(_Ping())


class _Broken:
    name = "broken"

    def setup(self, context: object) -> None:
        del context
        raise RuntimeError("setup failed")


class _SelfGrant:
    name = "self-grant"

    def setup(self, context: object) -> None:
        context.allow("echo")


class _Entry:
    def __init__(self, name: str, target: object) -> None:
        self.name = name
        self._target = target

    def load(self) -> object:
        return self._target


def test_plugin_tool_stays_denied_until_the_application_grants_it(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "plugin", name="plugin"))
    try:
        assert engine.capabilities["plugin_sdk"] is True
        record = engine.plugins.install(_Good())
        assert record.status is PluginStatus.LOADED
        assert [tool.name for tool in engine.agents.tools() if tool.name == "ping"] == ["ping"]
        engine.initialize()
        with pytest.raises(Exception) as caught:
            engine.agents.call("ping", "now")
        assert getattr(caught.value, "code", "") == "denied"
        assert engine.agents.grants() == []
        engine.agents.allow("ping")
        assert engine.agents.call("ping", "now") == "pong"
        engine.start()
        check = next(item for item in engine.health().checks if item.name == "plugins")
        assert check.status is CheckStatus.PASS
    finally:
        if engine.owns_runtime:
            engine.stop()
        engine.close()


def test_failed_plugin_warns_and_does_not_stop_boot(tmp_path: Path) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "failed", name="failed"))
    try:
        failed = engine.plugins.install(_Broken())
        assert failed.status is PluginStatus.FAILED
        assert "setup failed" in failed.detail
        granted = engine.plugins.install(_SelfGrant())
        assert granted.status is PluginStatus.FAILED
        assert engine.agents.grants() == []
        duplicate = engine.plugins.install(_Good())
        assert duplicate.status is PluginStatus.LOADED
        again = engine.plugins.install(_Good())
        assert again.status is PluginStatus.FAILED
        engine.initialize()
        engine.start()
        report = engine.health()
        assert report.status is HealthStatus.DEGRADED
        check = next(item for item in report.checks if item.name == "plugins")
        assert check.status is CheckStatus.WARN
    finally:
        if engine.owns_runtime:
            engine.stop()
        engine.close()


def test_load_uses_entry_points_and_records_discovery_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = LifeEngine(EngineConfig(data_dir=tmp_path / "load", name="load"))
    try:
        monkeypatch.setattr(
            "ai_life_engine.plugins.discovery.discover_plugins",
            lambda: [_Entry("sample", _Good), _Entry("broken", _Broken)],
        )
        loaded = engine.plugins.load()
        assert [row.status for row in loaded] == [PluginStatus.LOADED, PluginStatus.FAILED]

        def explode() -> list[object]:
            raise RuntimeError("index broken")

        monkeypatch.setattr("ai_life_engine.plugins.discovery.discover_plugins", explode)
        failed = engine.plugins.load()
        assert failed[-1].name == "discovery"
        assert failed[-1].status is PluginStatus.FAILED
    finally:
        engine.close()
