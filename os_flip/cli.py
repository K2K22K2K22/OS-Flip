"""Command line interface and interactive menu."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from . import __version__, power, ui
from .backends import BootBackend, get_backend
from .model import (
    AmbiguousTarget,
    BootError,
    BootTarget,
    TargetNotFound,
    UnsupportedOperation,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CANCELLED = 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="os-flip",
        description="Inspect and change the boot selection of Linux, Windows and macOS.",
    )
    parser.add_argument("--version", action="version", version=f"os-flip {__version__}")
    parser.add_argument("-l", "--list", action="store_true", help="list the boot targets and exit")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument(
        "--set-default", metavar="TARGET", help="change the permanent default to TARGET"
    )
    operation.add_argument(
        "--next-boot", metavar="TARGET", help="boot TARGET once at the next reboot"
    )
    operation.add_argument(
        "--enable-os-prober",
        action="store_true",
        help="enable os-prober in the GRUB configuration",
    )
    parser.add_argument("--reboot", action="store_true", help="reboot after a successful change")
    parser.add_argument(
        "--dry-run", action="store_true", help="show what would change without changing it"
    )
    parser.add_argument("--no-color", action="store_true", help="disable colored output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    ui.configure_color(False if arguments.no_color else None)

    try:
        backend = get_backend()
    except UnsupportedOperation as error:
        ui.error(str(error))
        return EXIT_ERROR

    try:
        if arguments.list:
            return _list(backend)
        if arguments.set_default:
            return _operation(backend, "set_default", arguments.set_default, arguments)
        if arguments.next_boot:
            return _operation(backend, "set_next_boot", arguments.next_boot, arguments)
        if arguments.enable_os_prober:
            return _os_prober(backend, arguments)
        if arguments.reboot:
            ui.error("--reboot belongs to --set-default or --next-boot")
            return EXIT_USAGE
        return menu(backend)
    except BootError as error:
        ui.error(str(error))
        return EXIT_ERROR
    except KeyboardInterrupt:
        ui.warning("cancelled")
        return EXIT_CANCELLED


def _list(backend: BootBackend) -> int:
    try:
        targets = backend.list_targets()
    except BootError as error:
        ui.error(str(error))
        return EXIT_ERROR
    _print_targets(backend, targets)
    return EXIT_OK


def _print_targets(backend: BootBackend, targets: list[BootTarget]) -> None:
    current = None
    try:
        current = backend.current_default()
    except BootError as error:
        ui.warning(f"the current default could not be read: {error}")
    current_id = current.identifier if current else None

    print()
    print("Available boot targets:")
    for index, target in enumerate(targets, start=1):
        marker = " (current default)" if current_id and target.identifier == current_id else ""
        details = []
        if target.identifier:
            details.append(f"id: {target.identifier}")
        if target.kind != "os":
            details.append(f"kind: {target.kind}")
        if target.note:
            details.append(target.note)
        suffix = f"   [{'; '.join(details)}]" if details else ""
        print(f"  {index}. {target.display_path}{marker}{suffix}")
    if not targets:
        print("  no boot targets were found")

    warnings = getattr(backend, "warnings", None)
    for warning in warnings or []:
        ui.warning(warning)


def _operation(backend: BootBackend, operation: str, selector: str, arguments) -> int:
    targets = backend.list_targets()
    target = resolve_target(targets, selector)
    action = getattr(backend, operation)

    plan = action(target, dry_run=True)
    if arguments.dry_run:
        _print_result(plan)
        return EXIT_OK if plan.ok else EXIT_ERROR

    if not plan.ok:
        _print_result(plan)
        return EXIT_ERROR

    result = action(target, dry_run=False)
    _print_result(result)
    if not result.ok:
        return EXIT_ERROR
    if result.reboot_required and arguments.reboot:
        return _reboot(backend)
    if result.reboot_required:
        ui.info("a reboot is required before the new selection is used")
    return EXIT_OK


def _os_prober(backend: BootBackend, arguments) -> int:
    plan = backend.enable_os_prober(dry_run=True)
    if arguments.dry_run:
        _print_result(plan)
        return EXIT_OK if plan.ok else EXIT_ERROR
    if not plan.ok:
        _print_result(plan)
        return EXIT_ERROR
    result = backend.enable_os_prober(dry_run=False)
    _print_result(result)
    return EXIT_OK if result.ok else EXIT_ERROR


def _print_result(result) -> None:
    if result.ok:
        ui.success(result.message)
    else:
        ui.error(result.message)
    for detail in result.details:
        print(f"    {detail}")


def _reboot(backend: BootBackend) -> int:
    if not _is_privileged(backend):
        ui.error("rebooting needs elevated privileges, run the same command with sudo")
        return EXIT_ERROR
    result = power.request_reboot()
    if result.ok:
        ui.success(result.message)
        return EXIT_OK
    ui.error(result.message)
    return EXIT_ERROR


def _is_privileged(backend: BootBackend) -> bool:
    checker = getattr(backend, "is_root", None) or getattr(backend, "is_admin", None)
    if checker is not None:
        return bool(checker())
    return hasattr(os, "geteuid") and os.geteuid() == 0


def resolve_target(targets: list[BootTarget], selector: str) -> BootTarget:
    """Resolve a selector into exactly one boot target."""
    text = selector.strip()
    if not text:
        raise TargetNotFound("no boot target was selected")
    if text.isdigit():
        index = int(text)
        if 1 <= index <= len(targets):
            return targets[index - 1]
        raise TargetNotFound(
            f"there is no boot target number {index}, the list has {len(targets)} entries"
        )
    matches = [
        target
        for target in targets
        if target.identifier and target.identifier.lower() == text.lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousTarget(f"'{text}' matches more than one boot target")
    titled = [target for target in targets if target.title == text]
    if len(titled) == 1:
        return titled[0]
    if len(titled) > 1:
        raise AmbiguousTarget(
            f"'{text}' matches {len(titled)} boot targets, use the number or the identifier"
        )
    raise TargetNotFound(
        f"'{text}' is not a known boot target, run os-flip --list to see the available targets"
    )


def menu(backend: BootBackend) -> int:
    """Interactive menu, drawn in the terminal that started OS-Flip."""
    ui.banner(backend.platform)
    try:
        targets = backend.list_targets()
    except BootError as error:
        ui.error(str(error))
        return EXIT_ERROR
    _print_targets(backend, targets)

    while True:
        print()
        print("Options:")
        print("  1. Set the permanent default")
        print("  2. Boot an operating system once at the next reboot")
        if backend.supports_os_prober:
            print("  3. Enable os-prober in the GRUB configuration")
            print("  4. Exit")
        else:
            print("  3. Exit")
        try:
            choice = input("Choose an option: ").strip()
        except (EOFError, KeyboardInterrupt):
            ui.warning("cancelled")
            return EXIT_CANCELLED

        if choice == "3" and not backend.supports_os_prober:
            ui.info("exiting")
            return EXIT_OK
        if choice == "4" and backend.supports_os_prober:
            ui.info("exiting")
            return EXIT_OK
        if choice == "1":
            operation = "set_default"
        elif choice == "2":
            operation = "set_next_boot"
        elif choice == "3" and backend.supports_os_prober:
            return _menu_os_prober(backend)
        else:
            ui.error("invalid option")
            continue

        target = _ask_target(targets)
        if target is None:
            continue

        action = getattr(backend, operation)
        plan = action(target, dry_run=True)
        _print_result(plan)
        if not plan.ok:
            continue
        if not _confirm("Apply this change? (y/N): "):
            ui.info("cancelled, nothing was changed")
            continue

        result = action(target, dry_run=False)
        _print_result(result)
        if not result.ok:
            continue
        if _confirm("Reboot now? (y/N): "):
            return _reboot(backend)


def _menu_os_prober(backend: BootBackend) -> int:
    plan = backend.enable_os_prober(dry_run=True)
    _print_result(plan)
    if not plan.ok:
        return EXIT_ERROR
    if not _confirm("Apply this change? (y/N): "):
        ui.info("cancelled, nothing was changed")
        return EXIT_OK
    result = backend.enable_os_prober(dry_run=False)
    _print_result(result)
    return EXIT_OK if result.ok else EXIT_ERROR


def _ask_target(targets: list[BootTarget]) -> BootTarget | None:
    try:
        answer = input("Select a boot target by number or identifier: ").strip()
    except (EOFError, KeyboardInterrupt):
        ui.warning("cancelled")
        return None
    try:
        return resolve_target(targets, answer)
    except BootError as error:
        ui.error(str(error))
        return None


def _confirm(prompt: str) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        ui.warning("cancelled")
        return False
    return answer in ("y", "yes")


if __name__ == "__main__":
    sys.exit(main())
