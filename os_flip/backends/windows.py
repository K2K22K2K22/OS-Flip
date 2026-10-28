"""Windows backend built on BCDEdit.

Boot Configuration Data is the documented interface for changing the Windows
boot selection. Entries are identified by their BCD identifier, never by their
display name. Firmware entries are listed for orientation only, they are not
modified by OS-Flip.
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
from typing import Callable

from ..model import (
    BootError,
    BootTarget,
    Capabilities,
    OperationResult,
    PrivilegeError,
)
from .base import BootBackend

Runner = Callable[..., subprocess.CompletedProcess]

IDENTIFIER_PATTERN = re.compile(r"^\{(?:[0-9a-fA-F-]{36}|[A-Za-z][A-Za-z0-9-]*)\}$")

FIRMWARE_NOTE = (
    "firmware entry, OS-Flip lists it for orientation and never changes firmware boot order"
)


def parse_bcd_output(text: str) -> list[dict[str, str]]:
    """Split ``bcdedit /enum`` output into blocks of key and value pairs."""
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            if current:
                blocks.append(current)
                current = {}
            continue
        if line.strip().startswith("---"):
            continue
        key, _, value = line.strip().partition(" ")
        if not value:
            continue
        current[key.strip().lower()] = value.strip()
    if current:
        blocks.append(current)
    return blocks


def parse_bcd_identifier(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if not value.startswith("{"):
        value = "{" + value + "}"
    return value if IDENTIFIER_PATTERN.match(value) else None


class WindowsBackend(BootBackend):
    """BCDEdit backend for Windows systems."""

    name = "Windows Boot Manager"
    platform = "Windows"

    def __init__(
        self,
        runner: Runner | None = None,
        which: Callable[[str], str | None] = shutil.which,
        is_admin: Callable[[], bool] | None = None,
    ) -> None:
        self._run = runner or subprocess.run
        self._which = which
        self._is_admin = is_admin

    # capabilities ----------------------------------------------------------

    def capabilities(self) -> Capabilities:
        return Capabilities(
            can_list=True,
            can_read_default=True,
            can_set_default=True,
            can_set_next_boot=True,
            read_requires_root=True,
            write_requires_root=True,
            limitations=(
                "BCDEdit needs an elevated Administrator shell, also for reading the store.",
                "Firmware entries are listed but never selected, "
                "firmware boot order is managed in setup.",
                "One time boots use the boot sequence of the Windows Boot Manager.",
            ),
        )

    def is_admin(self) -> bool:
        if self._is_admin is not None:
            return self._is_admin()
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return hasattr(os, "geteuid") and os.geteuid() == 0

    def _require_admin(self, action: str) -> None:
        if self.is_admin():
            return
        raise PrivilegeError(
            f"{action} needs an elevated Administrator shell, "
            "start the terminal with Run as administrator and try again"
        )

    # reading ---------------------------------------------------------------

    def _bcdedit(self, arguments: list[str]) -> str:
        command = self._which("bcdedit")
        if not command:
            raise BootError(
                "bcdedit was not found, it ships with Windows "
                "and is required to read or change boot entries"
            )
        completed = self._run([command, *arguments], check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise BootError(f"bcdedit {' '.join(arguments)} failed: {detail or 'unknown error'}")
        return completed.stdout

    def list_targets(self) -> list[BootTarget]:
        self._require_admin("reading the boot configuration")
        targets: list[BootTarget] = []

        for block in parse_bcd_output(self._bcdedit(["/enum", "OSLOADER"])):
            identifier = parse_bcd_identifier(block.get("identifier", ""))
            if not identifier:
                continue
            targets.append(
                BootTarget(
                    title=block.get("description") or identifier,
                    identifier=identifier,
                    kind="os",
                    source="bcdedit /enum OSLOADER",
                    note=block.get("path", ""),
                )
            )

        for block in parse_bcd_output(self._bcdedit(["/enum", "BOOTAPP"])):
            identifier = parse_bcd_identifier(block.get("identifier", ""))
            if not identifier:
                continue
            targets.append(
                BootTarget(
                    title=block.get("description") or identifier,
                    identifier=identifier,
                    kind="bootapp",
                    source="bcdedit /enum BOOTAPP",
                    note=block.get("path", ""),
                )
            )

        for block in parse_bcd_output(self._firmware_output()):
            identifier = parse_bcd_identifier(block.get("identifier", ""))
            if not identifier:
                continue
            targets.append(
                BootTarget(
                    title=block.get("description") or identifier,
                    identifier=identifier,
                    kind="firmware",
                    source="bcdedit /enum FIRMWARE",
                    selectable=False,
                    note=FIRMWARE_NOTE,
                )
            )

        return targets

    def _firmware_output(self) -> str:
        try:
            return self._bcdedit(["/enum", "FIRMWARE"])
        except BootError:
            return ""

    def current_default(self) -> BootTarget | None:
        self._require_admin("reading the current default")
        blocks = parse_bcd_output(self._bcdedit(["/enum", "BOOTMGR"]))
        for block in blocks:
            default = parse_bcd_identifier(block.get("default", ""))
            if not default:
                continue
            for target in self.list_targets():
                if target.identifier == default:
                    return target
            return BootTarget(
                title=default,
                identifier=default,
                kind="os",
                source="bcdedit /enum BOOTMGR",
            )
        return None

    # writing ---------------------------------------------------------------

    def set_default(self, target: BootTarget, dry_run: bool = False) -> OperationResult:
        return self._run_bcd(["/default", target], target, dry_run, "permanent default")

    def set_next_boot(self, target: BootTarget, dry_run: bool = False) -> OperationResult:
        return self._run_bcd(["/bootsequence", target], target, dry_run, "one time boot")

    def _run_bcd(
        self,
        arguments: list[str],
        target: BootTarget,
        dry_run: bool,
        operation: str,
    ) -> OperationResult:
        if not target.selectable:
            return OperationResult(
                False,
                f"{target.display_path} cannot be selected by OS-Flip",
                details=(target.note,),
            )
        identifier = target.identifier
        if not identifier:
            return OperationResult(
                False,
                f"{target.display_path} has no BCD identifier, the {operation} was refused",
            )
        if not IDENTIFIER_PATTERN.match(identifier):
            return OperationResult(
                False,
                f"'{identifier}' is not a valid BCD identifier, the {operation} was refused",
            )
        command = self._which("bcdedit")
        if not command:
            return OperationResult(False, "bcdedit was not found")
        full = [command, *arguments[:1], identifier]
        if dry_run:
            return OperationResult(
                True,
                f"would set the {operation} to {target.display_path}",
                details=(f"would run: {' '.join(full)}",),
            )
        self._require_admin(f"changing the {operation}")
        completed = self._run(full, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return OperationResult(
                False,
                f"could not set the {operation}",
                details=(
                    f"command: {' '.join(full)}",
                    f"exit code: {completed.returncode}",
                    detail,
                ),
            )
        reboot_required = arguments[0] == "/bootsequence"
        message = f"{operation} set to {target.display_path}"
        if reboot_required:
            message += ", a reboot is required"
        return OperationResult(
            True,
            message,
            details=(f"ran: {' '.join(full)}",),
            reboot_required=reboot_required,
        )
