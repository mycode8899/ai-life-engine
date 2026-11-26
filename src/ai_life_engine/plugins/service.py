# SPDX-License-Identifier: Apache-2.0
"""Load plugins from entry points or from a direct object.

``load`` is explicit. Constructing an engine does not scan installed
packages. A failed plugin is recorded and the rest still load.
"""

from __future__ import annotations

import re
from typing import Protocol

from ai_life_engine.observability import get_logger
from ai_life_engine.plugins.context import PluginContext
from ai_life_engine.plugins.models import PluginRecord, PluginStatus
from ai_life_engine.validation import clean_reason

logger = get_logger("ai_life_engine.plugins")

_NAME = re.compile(r"[a-z][a-z0-9._-]{0,63}")


class PluginHost(Protocol):
    """Narrow view of the runtime the plugin loader is allowed to touch."""

    @property
    def lock(self) -> object: ...

    def ensure_open(self) -> None: ...


class Plugins:
    """Track setup attempts for one engine handle."""

    def __init__(self, host: PluginHost, *, context: PluginContext) -> None:
        self._host = host
        self._context = context
        self._records: list[PluginRecord] = []

    def records(self) -> list[PluginRecord]:
        """Return setup attempts in load order."""

        with self._host.lock:
            self._host.ensure_open()
            return list(self._records)

    def install(self, plugin: object) -> PluginRecord:
        """Set up one plugin object. This does not read entry points."""

        self._host.ensure_open()
        return self._activate(plugin, source="plugin")

    def load(self) -> list[PluginRecord]:
        """Load every installed entry point in the plugin group."""

        self._host.ensure_open()
        from ai_life_engine.plugins import discovery

        try:
            found = list(discovery.discover_plugins())
        except Exception as exc:
            logger.warning("plugin discovery failed")
            record = self._remember("discovery", PluginStatus.FAILED, str(exc))
            return [record]
        loaded: list[PluginRecord] = []
        for entry in found:
            loaded.append(self._from_entry(entry))
        return loaded

    def _from_entry(self, entry: object) -> PluginRecord:
        name = getattr(entry, "name", "")
        label = name if isinstance(name, str) and name else "plugin"
        loader = getattr(entry, "load", None)
        if not callable(loader):
            return self._remember(label, PluginStatus.FAILED, "The entry point cannot be loaded.")
        try:
            target = loader()
        except Exception as exc:
            logger.warning("plugin import failed name=%s", label)
            return self._remember(label, PluginStatus.FAILED, str(exc))
        if isinstance(target, type):
            try:
                target = target()
            except Exception as exc:
                logger.warning("plugin init failed name=%s", label)
                return self._remember(label, PluginStatus.FAILED, str(exc))
        return self._activate(target, source=label)

    def _activate(self, plugin: object, *, source: str) -> PluginRecord:
        raw_name = getattr(plugin, "name", None)
        name = raw_name if isinstance(raw_name, str) else source
        setup = getattr(plugin, "setup", None)
        if not isinstance(name, str) or _NAME.fullmatch(name) is None or not callable(setup):
            return self._remember(
                source,
                PluginStatus.FAILED,
                "A plugin needs a lowercase name and setup().",
            )
        with self._host.lock:
            if any(
                record.name == name and record.status is PluginStatus.LOADED
                for record in self._records
            ):
                return self._remember(name, PluginStatus.FAILED, "That plugin is already loaded.")
        try:
            setup(self._context)
        except Exception as exc:
            logger.warning("plugin setup failed name=%s", name)
            return self._remember(name, PluginStatus.FAILED, str(exc))
        logger.info("plugin loaded name=%s", name)
        return self._remember(name, PluginStatus.LOADED, "Loaded.")

    def _remember(self, name: str, status: PluginStatus, detail: str) -> PluginRecord:
        try:
            cleaned = clean_reason(detail, limit=200)
        except ValueError:
            cleaned = "Plugin setup failed."
        safe_name = name if isinstance(name, str) and _NAME.fullmatch(name) else "plugin"
        record = PluginRecord(name=safe_name, status=status, detail=cleaned)
        with self._host.lock:
            self._host.ensure_open()
            self._records.append(record)
        return record
