"""Interface implemented by every platform backend."""

from __future__ import annotations

from typing import Optional

from ..model import BootTarget, Capabilities, OperationResult, UnsupportedOperation


class BootBackend:
    """Discovers boot targets and changes the boot selection of one platform."""

    name = "generic"
    platform = "generic"
    supports_os_prober = False

    def capabilities(self) -> Capabilities:
        raise NotImplementedError

    def list_targets(self) -> list[BootTarget]:
        raise NotImplementedError

    def current_default(self) -> Optional[BootTarget]:
        raise NotImplementedError

    def set_default(self, target: BootTarget, dry_run: bool = False) -> OperationResult:
        raise NotImplementedError

    def set_next_boot(self, target: BootTarget, dry_run: bool = False) -> OperationResult:
        raise NotImplementedError

    def enable_os_prober(self, dry_run: bool = False) -> OperationResult:
        raise UnsupportedOperation("this platform has no os-prober to enable")
