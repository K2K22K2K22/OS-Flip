"""Data model shared by every platform backend."""

from __future__ import annotations

from dataclasses import dataclass


class BootError(Exception):
    """Base class for failures that are safe to report to the user."""


class UnsupportedOperation(BootError):
    """The backend cannot perform the requested operation on this system."""


class TargetNotFound(BootError):
    """The selector did not match a boot target."""


class AmbiguousTarget(BootError):
    """The selector matched more than one boot target."""


class PrivilegeError(BootError):
    """The current user lacks the privileges required for the operation."""


@dataclass(frozen=True)
class BootTarget:
    """A boot target as reported by a platform backend."""

    title: str
    identifier: str | None = None
    path: tuple[str, ...] = ()
    path_ids: tuple[str | None, ...] = ()
    kind: str = "os"
    source: str = ""
    selectable: bool = True
    note: str = ""

    @property
    def display_path(self) -> str:
        return " > ".join((*self.path, self.title))

    @property
    def key(self) -> str:
        return self.identifier or self.display_path


@dataclass(frozen=True)
class Capabilities:
    """What a backend can do on the current machine."""

    can_list: bool = True
    can_read_default: bool = True
    can_set_default: bool = True
    can_set_next_boot: bool = True
    read_requires_root: bool = False
    write_requires_root: bool = True
    limitations: tuple[str, ...] = ()

    def supports(self, operation: str) -> bool:
        return {
            "list": self.can_list,
            "read default": self.can_read_default,
            "set default": self.can_set_default,
            "one time boot": self.can_set_next_boot,
        }.get(operation, True)

    def reason(self, operation: str) -> str:
        if self.supports(operation):
            return ""
        return f"{operation} is not available on this system"


@dataclass
class OperationResult:
    """Outcome of a backend operation."""

    ok: bool
    message: str
    details: tuple[str, ...] = ()
    reboot_required: bool = False
    changed_files: tuple[str, ...] = ()
