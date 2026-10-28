"""macOS backend built on systemsetup and bless.

``systemsetup`` reports and changes the startup disk, which is the documented
interface for a persistent selection. One time boots use ``bless`` with
architecture specific arguments: on Intel EFI systems the boot device is set
for the next boot only, on Apple Silicon the platform specific options and the
owner authorization rules apply.
"""

from __future__ import annotations

import getpass
import os
import platform
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Callable

from ..model import (
    BootError,
    BootTarget,
    Capabilities,
    OperationResult,
    PrivilegeError,
    UnsupportedOperation,
)
from .base import BootBackend

Runner = Callable[..., subprocess.CompletedProcess]

APPLE_SILICON = "apple_silicon"
INTEL = "intel"


def parse_startup_disks(output: str) -> list[BootTarget]:
    """Read the output of ``systemsetup -liststartupdisks``.

    The command prints a volume name and, indented below it, the path of the
    startup disk. Both forms are accepted.
    """
    targets: list[BootTarget] = []
    name: str | None = None
    path: str | None = None

    def flush() -> None:
        nonlocal name, path
        if name is None:
            name = path
            path = None
        if name:
            targets.append(
                BootTarget(
                    title=name,
                    identifier=path or name,
                    path=(path,) if path else (),
                    kind="os",
                    source="systemsetup -liststartupdisks",
                    selectable=bool(path),
                    note=(
                        ""
                        if path
                        else "the startup disk path was not reported, selection is not possible"
                    ),
                )
            )
        name = None
        path = None

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith((" ", "\t")):
            candidate = line.strip()
            if candidate.startswith("/"):
                path = candidate
            continue
        if name is not None:
            flush()
        name = line.strip()
    if name is not None:
        flush()
    return targets


def parse_current_disk(output: str) -> str | None:
    for line in output.splitlines():
        if ":" in line:
            return line.split(":", 1)[1].strip() or None
    return None


class MacOSBackend(BootBackend):
    """systemsetup and bless backend for macOS systems."""

    name = "macOS Startup Disk"
    platform = "Darwin"

    def __init__(
        self,
        runner: Runner | None = None,
        which: Callable[[str], str | None] = shutil.which,
        machine: Callable[[], str] | None = None,
        username: str | None = None,
        is_root: Callable[[], bool] | None = None,
    ) -> None:
        self._run = runner or subprocess.run
        self._which = which
        self._machine = machine
        self._username = username
        self._is_root = is_root

    # capabilities ----------------------------------------------------------

    def capabilities(self) -> Capabilities:
        architecture = self.architecture()
        if architecture == APPLE_SILICON:
            one_time_note = (
                "Apple Silicon uses bless --mount with --nextonly and owner authorization, "
                "the Startup Security Utility still decides which systems may be started."
            )
        else:
            one_time_note = (
                "Intel EFI systems use bless --mount with --setBoot --nextonly, "
                "which selects the volume for the next boot only."
            )
        return Capabilities(
            can_list=True,
            can_read_default=True,
            can_set_default=True,
            can_set_next_boot=True,
            read_requires_root=True,
            write_requires_root=True,
            limitations=(
                "systemsetup and bless need root privileges.",
                one_time_note,
                "The persistent startup disk and the one time boot are different operations, "
                "only bless --nextonly changes the next boot.",
            ),
        )

    def architecture(self) -> str:
        machine = self._machine() if self._machine else platform.machine()
        if machine.lower() in ("arm64", "aarch64"):
            return APPLE_SILICON
        return INTEL

    def username(self) -> str:
        if self._username:
            return self._username
        try:
            return getpass.getuser()
        except (KeyError, OSError):
            return os.environ.get("USER", "unknown")

    def is_root(self) -> bool:
        if self._is_root is not None:
            return self._is_root()
        return hasattr(os, "geteuid") and os.geteuid() == 0

    def _require_root(self, action: str) -> None:
        if self.is_root():
            return
        raise PrivilegeError(f"{action} needs root privileges, run the command with sudo")

    # reading ---------------------------------------------------------------

    def _systemsetup(self, arguments: list[str]) -> str:
        command = self._which("systemsetup")
        if not command:
            raise BootError(
                "systemsetup was not found, it is part of macOS "
                "and is required to read or change the startup disk"
            )
        completed = self._run([command, *arguments], check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise BootError(
                f"systemsetup {' '.join(arguments)} failed: {detail or 'unknown error'}"
            )
        return completed.stdout

    def list_targets(self) -> list[BootTarget]:
        self._require_root("reading the startup disks")
        targets = parse_startup_disks(self._systemsetup(["-liststartupdisks"]))
        return [
            replace(target, note="macOS system volume")
            if target.path and self._is_macos_volume(Path(target.path[0]))
            else target
            for target in targets
        ]

    def _is_macos_volume(self, mount_point: Path) -> bool:
        return (mount_point / "System/Library/CoreServices").is_dir()

    def current_default(self) -> BootTarget | None:
        self._require_root("reading the current startup disk")
        current = parse_current_disk(self._systemsetup(["-getstartupdisk"]))
        if not current:
            return None
        for target in self.list_targets():
            if target.identifier == current or target.title == current:
                return target
            if target.identifier and Path(target.identifier).name == current:
                return target
        return BootTarget(
            title=current,
            identifier=current,
            kind="os",
            source="systemsetup -getstartupdisk",
        )

    # writing ---------------------------------------------------------------

    def set_default(self, target: BootTarget, dry_run: bool = False) -> OperationResult:
        if not target.selectable:
            return OperationResult(
                False,
                f"{target.display_path} cannot be selected safely",
                details=(target.note,),
            )
        command = self._which("systemsetup")
        if not command:
            return OperationResult(False, "systemsetup was not found")
        full = [command, "-setstartupdisk", target.identifier or target.title]
        if dry_run:
            return OperationResult(
                True,
                f"would set the startup disk to {target.display_path}",
                details=(f"would run: {' '.join(full)}",),
            )
        self._require_root("changing the startup disk")
        completed = self._run(full, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return OperationResult(
                False,
                "could not set the startup disk",
                details=(
                    f"command: {' '.join(full)}",
                    f"exit code: {completed.returncode}",
                    detail,
                    "on Apple Silicon the Startup Security Utility "
                    "controls which systems may be started",
                ),
            )
        return OperationResult(
            True,
            f"startup disk set to {target.display_path}",
            details=(f"ran: {' '.join(full)}",),
        )

    def set_next_boot(self, target: BootTarget, dry_run: bool = False) -> OperationResult:
        if not target.selectable or not target.path:
            raise UnsupportedOperation(
                "a one time boot needs the mounted path of the startup disk, "
                "which systemsetup did not report for this volume"
            )
        command = self._which("bless")
        if not command:
            return OperationResult(False, "bless was not found")
        mount_point = target.path[0]
        if self.architecture() == APPLE_SILICON:
            full = [command, "--mount", mount_point, "--nextonly", "--user", self.username()]
            note = (
                "Apple Silicon selects the next boot with bless --mount --nextonly, "
                "owner authorization may be requested"
            )
        else:
            full = [command, "--mount", mount_point, "--setBoot", "--nextonly"]
            note = "Intel EFI selects the next boot with bless --mount --setBoot --nextonly"
        if dry_run:
            return OperationResult(
                True,
                f"would boot {target.display_path} once",
                details=(f"would run: {' '.join(full)}", note),
            )
        self._require_root("scheduling a one time boot")
        completed = self._run(full, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return OperationResult(
                False,
                "could not schedule the one time boot",
                details=(
                    f"command: {' '.join(full)}",
                    f"exit code: {completed.returncode}",
                    detail,
                    note,
                ),
            )
        return OperationResult(
            True,
            f"one time boot set to {target.display_path}, a reboot is required",
            details=(f"ran: {' '.join(full)}", note),
            reboot_required=True,
        )
