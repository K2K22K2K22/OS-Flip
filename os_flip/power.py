"""Explicit reboot requests.

Nothing in this module runs unless a caller has already obtained consent from
the user. Selecting a boot target never reaches this code on its own.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from typing import Callable

from .model import OperationResult, UnsupportedOperation

Runner = Callable[..., subprocess.CompletedProcess]


def reboot_command() -> list[str]:
    """Return the platform specific reboot command."""
    system = platform.system()
    if system == "Linux":
        if shutil.which("systemctl"):
            return ["systemctl", "reboot"]
        return ["shutdown", "-r", "now"]
    if system == "Windows":
        return ["shutdown", "/r", "/t", "0"]
    if system == "Darwin":
        return ["shutdown", "-r", "now"]
    raise UnsupportedOperation(f"reboot is not supported on {system}")


def request_reboot(run: Runner = None) -> OperationResult:
    """Ask the operating system to reboot now."""
    runner = run or subprocess.run
    command = reboot_command()
    completed = runner(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return OperationResult(False, f"reboot request failed: {detail or 'unknown error'}")
    return OperationResult(True, "reboot requested")
