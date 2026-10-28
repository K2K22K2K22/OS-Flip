"""Platform backend selection."""

from __future__ import annotations

import platform

from ..model import UnsupportedOperation
from .base import BootBackend
from .linux import LinuxBackend
from .macos import MacOSBackend
from .windows import WindowsBackend

__all__ = [
    "BootBackend",
    "LinuxBackend",
    "MacOSBackend",
    "WindowsBackend",
    "get_backend",
]


def get_backend(system: str = "") -> BootBackend:
    """Return the backend for the running platform."""
    name = system or platform.system()
    if name == "Linux":
        return LinuxBackend()
    if name == "Windows":
        return WindowsBackend()
    if name == "Darwin":
        return MacOSBackend()
    raise UnsupportedOperation(
        f"OS-Flip supports Linux, Windows and macOS, this system reports {name}"
    )
