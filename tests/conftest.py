# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from ai_life_engine import EngineConfig, LifeEngine
from ai_life_engine.core.process import process_exists


@pytest.fixture
def config(tmp_path) -> EngineConfig:
    return EngineConfig(data_dir=tmp_path / "runtime", name="test")


@pytest.fixture
def engine(config: EngineConfig):
    instance = LifeEngine(config)
    try:
        yield instance
    finally:
        try:
            if instance.owns_runtime:
                instance.stop(reason="test cleanup")
        finally:
            instance.close()


@pytest.fixture
def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    pid = proc.pid
    proc.kill()
    proc.wait(timeout=5)
    for _ in range(50):
        if not process_exists(pid):
            return pid
        time.sleep(0.01)
    pytest.skip("The exited process id is still visible.")
