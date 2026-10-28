"""Shared test helpers.

No test in this suite may touch the boot configuration of the host and no test
may reboot, shut down or restart the machine. The guard below turns any
attempt to run a reboot or shutdown command into a test failure.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "grub"

FORBIDDEN_COMMANDS = (
    "reboot",
    "shutdown",
    "systemctl",
    "halt",
    "poweroff",
    "restart-computer",
    "restartcomputer",
    "init 0",
)


def load_fixture(name: str) -> str:
    """Read a GRUB configuration fixture."""
    return (FIXTURES / name).read_text(encoding="utf-8")



class FakeRunner:
    """Record subprocess calls and replay canned results."""

    def __init__(
        self,
        results: dict[str, subprocess.CompletedProcess] | None = None,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.calls: list[list[str]] = []
        self.results = results or {}
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, command: Sequence[str], *args, **kwargs) -> subprocess.CompletedProcess:
        argv = [str(part) for part in command]
        self.calls.append(argv)
        joined = " ".join(argv)
        for pattern, result in self.results.items():
            if pattern in joined:
                return result
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)

    @property
    def commands(self) -> list[str]:
        return [" ".join(call) for call in self.calls]

    def reset(self) -> None:
        self.calls.clear()


def completed(
    command: Sequence[str],
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(list(command), returncode, stdout, stderr)


@pytest.fixture(autouse=True)
def no_reboot_guard(monkeypatch):
    """Fail the test when any code under test runs a reboot or shutdown."""
    original = subprocess.run

    def guarded(command, *args, **kwargs):
        if isinstance(command, (list, tuple)):
            parts = [str(part) for part in command]
        else:
            parts = [str(command)]
        text = " ".join(parts).lower()
        for token in FORBIDDEN_COMMANDS:
            if token in text:
                raise AssertionError(f"a test tried to run a forbidden command: {text}")
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)
    yield
