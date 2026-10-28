"""Linux backend built on GRUB2.

Reading boot entries never touches the system. Changing a default is done
through the configuration file or the GRUB tools that the distribution
provides, always with a backup and a rollback when regeneration fails.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..model import (
    AmbiguousTarget,
    BootError,
    BootTarget,
    Capabilities,
    OperationResult,
    PrivilegeError,
)
from .base import BootBackend
from .grub_parser import parse_grub_config

Runner = Callable[..., subprocess.CompletedProcess]

DEFAULT_CONFIG = Path("/etc/default/grub")
GRUB_CONFIG_CANDIDATES = (
    Path("/boot/grub/grub.cfg"),
    Path("/boot/grub2/grub.cfg"),
)
GRUBENV_CANDIDATES = (
    Path("/boot/grub/grubenv"),
    Path("/boot/grub2/grubenv"),
)
BLS_DIRECTORY = Path("/boot/loader/entries")
OS_RELEASE = Path("/etc/os-release")


def atomic_write(path: Path, text: str) -> None:
    """Replace a file in one step while keeping its mode and ownership."""
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        stat = path.stat()
        os.chmod(temporary, stat.st_mode & 0o7777)
        try:
            os.chown(temporary, stat.st_uid, stat.st_gid)
        except (OSError, AttributeError):
            pass
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class LinuxBackend(BootBackend):
    """GRUB2 backend for Linux systems."""

    name = "GRUB2"
    platform = "Linux"
    supports_os_prober = True

    def __init__(
        self,
        default_config: Path | None = None,
        grub_config: Path | None = None,
        grubenv: Path | None = None,
        bls_directory: Path | None = None,
        runner: Runner | None = None,
        which: Callable[[str], str | None] = shutil.which,
        is_root: Callable[[], bool] | None = None,
    ) -> None:
        self.default_config = Path(default_config) if default_config else DEFAULT_CONFIG
        self._grub_config = Path(grub_config) if grub_config else None
        self._grubenv = Path(grubenv) if grubenv else None
        self._bls_directory = Path(bls_directory) if bls_directory else BLS_DIRECTORY
        self._run = runner or subprocess.run
        self._which = which
        self._is_root = is_root
        self.warnings: list[str] = []

    # paths -----------------------------------------------------------------

    def grub_config_path(self) -> Path | None:
        if self._grub_config is not None:
            return self._grub_config
        for candidate in GRUB_CONFIG_CANDIDATES:
            if candidate.exists():
                return candidate
        efi = Path("/boot/efi/EFI")
        if efi.is_dir():
            for candidate in sorted(efi.glob("*/grub.cfg")):
                return candidate
        return None

    def grubenv_path(self) -> Path | None:
        if self._grubenv is not None:
            return self._grubenv
        for candidate in GRUBENV_CANDIDATES:
            if candidate.exists():
                return candidate
        return None

    def distro(self) -> str:
        try:
            content = OS_RELEASE.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "unknown"
        values = {}
        for line in content.splitlines():
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"')
        identifier = values.get("ID", "").lower()
        like = values.get("ID_LIKE", "").lower()
        for name in ("debian", "ubuntu", "fedora", "rhel", "centos", "arch", "suse", "opensuse"):
            if name in identifier or name in like:
                return name
        return identifier or "unknown"

    def is_root(self) -> bool:
        if self._is_root is not None:
            return self._is_root()
        return hasattr(os, "geteuid") and os.geteuid() == 0

    # capabilities ----------------------------------------------------------

    def capabilities(self) -> Capabilities:
        return Capabilities(
            can_list=True,
            can_read_default=True,
            can_set_default=True,
            can_set_next_boot=True,
            read_requires_root=False,
            write_requires_root=True,
            limitations=(
                "Permanent defaults are stored in /etc/default/grub and need a GRUB regeneration.",
                "One time boots use grub-reboot and do not regenerate the configuration.",
            ),
        )

    # reading ---------------------------------------------------------------

    def list_targets(self) -> list[BootTarget]:
        config = self.grub_config_path()
        if config is None or not config.exists():
            raise BootError(
                "no GRUB configuration was found. Looked at "
                + ", ".join(str(path) for path in GRUB_CONFIG_CANDIDATES)
            )
        try:
            text = config.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            raise BootError(f"could not read {config}: {error}") from error

        entries, warnings = parse_grub_config(text, str(config))
        targets: list[BootTarget] = []
        for entry in entries:
            targets.append(
                BootTarget(
                    title=entry.title,
                    identifier=entry.entry_id,
                    path=entry.path,
                    path_ids=entry.path_ids,
                    kind="submenu" if entry.is_submenu else "os",
                    source=str(config),
                    selectable=True,
                    note=entry.entry_type,
                )
            )
        targets.extend(self._bls_targets())
        self.warnings = warnings
        return targets

    def _bls_targets(self) -> list[BootTarget]:
        targets: list[BootTarget] = []
        directory = self._bls_directory
        if not directory.is_dir():
            return targets
        for path in sorted(directory.glob("*.conf")):
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            values = {}
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                key, _, value = stripped.partition(" ")
                values[key.strip().lower()] = value.strip()
            title = values.get("title")
            if not title:
                continue
            identifier = values.get("id")
            targets.append(
                BootTarget(
                    title=title,
                    identifier=identifier,
                    kind="bls",
                    source=str(path),
                    selectable=bool(identifier),
                    note=(
                        "Boot Loader Specification entry"
                        if identifier
                        else "Boot Loader Specification entry without a stable id, select by number"
                    ),
                )
            )
        return targets

    def current_default(self) -> BootTarget | None:
        value = self.read_setting("GRUB_DEFAULT")
        if value is None:
            return None
        if value == "saved":
            saved = self.read_saved_entry()
            if not saved:
                return None
            return self._resolve(saved)
        return self._resolve(value)

    def read_setting(self, name: str) -> str | None:
        try:
            content = self.default_config.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith(name + "="):
                continue
            value = stripped.split("=", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
        return None

    def read_saved_entry(self) -> str | None:
        command = self._which("grub-editenv") or self._which("grub2-editenv")
        if not command:
            return None
        try:
            completed = self._run([command, "list"], check=False, capture_output=True, text=True)
        except OSError:
            return None
        if completed.returncode != 0:
            return None
        for line in completed.stdout.splitlines():
            if line.startswith("saved_entry="):
                return line.split("=", 1)[1].strip() or None
        return None

    def _resolve(self, spec: str) -> BootTarget | None:
        targets = self.list_targets()
        if spec.isdigit():
            top_level = [target for target in targets if not target.path]
            index = int(spec)
            if 0 <= index < len(top_level):
                return top_level[index]
            return None
        if ">" in spec:
            parts = _split_path(spec)
            for target in targets:
                if list(target.path) + [target.title] == parts:
                    return target
            return None
        matches = [target for target in targets if target.identifier == spec]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousTarget(f"'{spec}' matches more than one boot entry")
        titled = [target for target in targets if target.title == spec]
        if len(titled) == 1:
            return titled[0]
        if len(titled) > 1:
            raise AmbiguousTarget(
                f"'{spec}' matches {len(titled)} boot entries, select one by number or identifier"
            )
        return None

    # writing ---------------------------------------------------------------

    def set_default(self, target: BootTarget, dry_run: bool = False) -> OperationResult:
        if not target.selectable:
            return OperationResult(False, f"{target.display_path} cannot be selected safely")
        spec = self._grub_spec(target)
        config = self.default_config

        if not config.exists():
            return OperationResult(
                False,
                f"{config} was not found, the permanent default cannot be changed",
            )

        current = self.read_setting("GRUB_DEFAULT")
        if current == "saved":
            return self._set_saved_default(target, spec, dry_run)

        command = self._regeneration_command()
        if command is None:
            return OperationResult(
                False,
                "no supported GRUB regeneration command was found "
                "(looked for update-grub, grub2-mkconfig and grub-mkconfig)",
            )

        if dry_run:
            return OperationResult(
                True,
                f"would set the permanent default to {target.display_path}",
                details=(
                    f"identifier used by GRUB: {spec}",
                    f"would update {config}",
                    f"would run: {' '.join(command)}",
                ),
            )

        if not self.is_root():
            raise PrivilegeError(
                "changing the permanent default needs root privileges, "
                f"run: sudo {self._command_hint()}"
            )

        backup = self._backup(config)
        try:
            self._write_setting(config, "GRUB_DEFAULT", spec)
        except OSError as error:
            return OperationResult(
                False,
                f"could not update {config}: {error}",
                details=(f"backup of the previous configuration: {backup}",),
            )

        completed = self._run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            restored = self._restore(backup, config)
            if restored:
                message = "GRUB regeneration failed, the previous configuration was restored"
            else:
                message = (
                    "GRUB regeneration failed, the previous configuration could not be restored"
                )
            return OperationResult(
                False,
                message,
                details=(
                    f"command: {' '.join(command)}",
                    f"exit code: {completed.returncode}",
                    f"output: {detail}" if detail else "no output",
                    f"backup: {backup}",
                    f"restore: {'succeeded' if restored else 'failed'}",
                    "the generated boot configuration may still need repair, see the backup file",
                ),
            )

        return OperationResult(
            True,
            f"permanent default set to {target.display_path}",
            details=(
                f"identifier used by GRUB: {spec}",
                f"updated {config}",
                f"ran: {' '.join(command)}",
            ),
            changed_files=(str(config),),
        )

    def _set_saved_default(self, target: BootTarget, spec: str, dry_run: bool) -> OperationResult:
        command = self._which("grub-set-default") or self._which("grub2-set-default")
        if not command:
            return OperationResult(
                False,
                "GRUB_DEFAULT is saved but neither grub-set-default "
                "nor grub2-set-default was found",
            )
        if dry_run:
            return OperationResult(
                True,
                f"would set the permanent default to {target.display_path}",
                details=(f"would run: {command} {spec}",),
            )
        if not self.is_root():
            raise PrivilegeError(
                "changing the permanent default needs root privileges, "
                f"run: sudo {self._command_hint()}"
            )
        completed = self._run([command, spec], check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return OperationResult(
                False,
                "could not store the saved default",
                details=(
                    f"command: {command} {spec}",
                    f"exit code: {completed.returncode}",
                    detail,
                ),
            )
        return OperationResult(
            True,
            f"permanent default set to {target.display_path}",
            details=(f"ran: {command} {spec}",),
        )

    def set_next_boot(self, target: BootTarget, dry_run: bool = False) -> OperationResult:
        if not target.selectable:
            return OperationResult(False, f"{target.display_path} cannot be selected safely")
        spec = self._grub_spec(target)
        command = self._which("grub-reboot") or self._which("grub2-reboot")
        if not command:
            return OperationResult(
                False,
                "neither grub-reboot nor grub2-reboot was found, one time boots are not available",
            )
        if dry_run:
            return OperationResult(
                True,
                f"would boot {target.display_path} once",
                details=(f"would run: {command} {spec}", "no GRUB regeneration is needed"),
            )
        if not self.is_root():
            raise PrivilegeError(
                "scheduling a one time boot needs root privileges, "
                f"run: sudo {self._command_hint()}"
            )
        completed = self._run([command, spec], check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return OperationResult(
                False,
                "could not schedule the one time boot",
                details=(
                    f"command: {command} {spec}",
                    f"exit code: {completed.returncode}",
                    detail,
                ),
            )
        return OperationResult(
            True,
            f"one time boot set to {target.display_path}, a reboot is required",
            details=(f"ran: {command} {spec}",),
            reboot_required=True,
        )

    def enable_os_prober(self, dry_run: bool = False) -> OperationResult:
        if not self._which("os-prober"):
            return OperationResult(
                False,
                "os-prober is not installed, install it first "
                "if other operating systems should be listed",
            )
        config = self.default_config
        if not config.exists():
            return OperationResult(False, f"{config} was not found")
        current = self.read_setting("GRUB_DISABLE_OS_PROBER")
        if current == "false":
            return OperationResult(True, "os-prober is already enabled")
        command = self._regeneration_command()
        if command is None:
            return OperationResult(
                False,
                "no supported GRUB regeneration command was found, "
                "the configuration was not changed",
            )
        if dry_run:
            return OperationResult(
                True,
                "would enable os-prober",
                details=(
                    f"would set GRUB_DISABLE_OS_PROBER=false in {config}",
                    f"would run: {' '.join(command)}",
                ),
            )
        if not self.is_root():
            raise PrivilegeError(
                f"enabling os-prober needs root privileges, run: sudo {self._command_hint()}"
            )
        backup = self._backup(config)
        try:
            self._write_setting(config, "GRUB_DISABLE_OS_PROBER", "false")
        except OSError as error:
            return OperationResult(
                False,
                f"could not update {config}: {error}",
                details=(f"backup of the previous configuration: {backup}",),
            )
        completed = self._run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            restored = self._restore(backup, config)
            return OperationResult(
                False,
                "GRUB regeneration failed"
                + (", the previous configuration was restored" if restored else ""),
                details=(
                    f"command: {' '.join(command)}",
                    f"exit code: {completed.returncode}",
                    f"output: {detail}" if detail else "no output",
                    f"backup: {backup}",
                ),
            )
        return OperationResult(
            True,
            "os-prober enabled and the GRUB configuration regenerated",
            details=(f"updated {config}", f"ran: {' '.join(command)}"),
            changed_files=(str(config),),
        )

    # helpers ---------------------------------------------------------------

    def _grub_spec(self, target: BootTarget) -> str:
        if not target.path:
            return target.identifier or target.title
        components: list[str] = []
        identifiers = list(target.path_ids) + [None] * (len(target.path) - len(target.path_ids))
        for title, identifier in zip(target.path, identifiers):
            components.append(identifier or title)
        components.append(target.identifier or target.title)
        return ">".join(component.replace(">", ">>") for component in components)

    def _regeneration_command(self) -> list[str] | None:
        if self._which("update-grub"):
            return ["update-grub"]
        for name in ("grub2-mkconfig", "grub-mkconfig"):
            if not self._which(name):
                continue
            config = self.grub_config_path()
            if config is not None and config.exists():
                target = str(config)
            else:
                target = str(self._fallback_grub_config())
            return [name, "-o", target]
        return None

    def _fallback_grub_config(self) -> Path:
        distro = self.distro()
        if distro in ("fedora", "rhel", "centos", "suse", "opensuse"):
            return Path("/boot/grub2/grub.cfg")
        return Path("/boot/grub/grub.cfg")

    def _backup(self, config: Path) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = config.with_name(f"{config.name}.bak.{stamp}.{secrets.token_hex(3)}")
        shutil.copy2(config, backup)
        return backup

    def _restore(self, backup: Path, config: Path) -> bool:
        try:
            original = backup.read_text(encoding="utf-8")
            atomic_write(config, original)
        except OSError:
            return False
        return True

    def _write_setting(self, config: Path, name: str, value: str) -> None:
        original = config.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)
        replacement = f'{name}="{value}"\n'
        replaced = False
        for index, line in enumerate(lines):
            if line.strip().startswith(name + "="):
                lines[index] = replacement
                replaced = True
                break
        if not replaced:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(replacement)
        atomic_write(config, "".join(lines))

    def _command_hint(self) -> str:
        return "os-flip"


def _split_path(spec: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(spec):
        char = spec[index]
        if char == ">":
            if index + 1 < len(spec) and spec[index + 1] == ">":
                current.append(">")
                index += 2
                continue
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    parts.append("".join(current))
    return parts
