# SPDX-License-Identifier: Apache-2.0
"""Portable process-liveness checks used for crash recovery."""

from __future__ import annotations

import os


def process_exists(pid: int) -> bool:
    """Return True when ``pid`` appears to belong to a live process.

    A recycled pid can produce a false positive. Phase 1 treats that as an
    active claim and will not take the runtime over. The caller must pass a
    positive integer; signal 0 is never delivered to pid 0.
    """

    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_exists(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True
