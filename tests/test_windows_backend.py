"""Tests for the Windows BCDEdit backend.

BCDEdit is mocked completely, the tests assert the exact argument lists and
never run a native boot command.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import FakeRunner, completed

from os_flip.backends.windows import WindowsBackend, parse_bcd_output
from os_flip.model import BootError, PrivilegeError

OSLOADER_OUTPUT = """\
Windows Boot Loader
-------------------
identifier              {cbd971bf-b7b8-4885-951a-fa03044f5d71}
device                  partition=C:
path                    \\Windows\\system32\\winload.exe
description             Windows 11 Pro
locale                  en-US
inherit                 {default}
"""

BOOTAPP_OUTPUT = """\
Windows Boot Application
------------------------
identifier              {9dea862c-5cdd-4e70-acc1-f32b344d4795}
device                  partition=C:
path                    \\Windows\\System32\\winload.exe
description             Windows 11 Pro
"""

FIRMWARE_OUTPUT = """\
Firmware Applications
---------------------
identifier              {fwmgmt-8b8c4d5e-0f1a-4c3b-9d2e-5f6a7b8c9d0e}
device                  partition=2
path                    \\EFI\\BOOT\\BOOTX64.EFI
description             UEFI Firmware
"""

BOOTMGR_OUTPUT = """\
Windows Boot Manager
--------------------
identifier              {bootmgr}
device                  partition=\\Device\\HarddiskVolume2
path                    \\EFI\\Microsoft\\Boot\\bootmgfw.efi
description             Windows Boot Manager
default                 {cbd971bf-b7b8-4885-951a-fa03044f5d71}
timeout                 10
"""


def build_backend(runner=None, admin=True):
    results = {
        "/enum OSLOADER": completed(["bcdedit", "/enum", "OSLOADER"], stdout=OSLOADER_OUTPUT),
        "/enum BOOTAPP": completed(["bcdedit", "/enum", "BOOTAPP"], stdout=BOOTAPP_OUTPUT),
        "/enum FIRMWARE": completed(
            ["bcdedit", "/enum", "FIRMWARE"], stdout=FIRMWARE_OUTPUT
        ),
        "/enum BOOTMGR": completed(["bcdedit", "/enum", "BOOTMGR"], stdout=BOOTMGR_OUTPUT),
    }
    if runner is None:
        runner = FakeRunner(results)
    else:
        runner.results.update(results)
    return WindowsBackend(
        runner=runner,
        which=lambda name: "C:\\Windows\\System32\\bcdedit.exe" if name == "bcdedit" else None,
        is_admin=lambda: admin,
    )


def test_bcd_output_is_split_into_blocks():
    blocks = parse_bcd_output(OSLOADER_OUTPUT)

    assert blocks[0]["identifier"] == "{cbd971bf-b7b8-4885-951a-fa03044f5d71}"
    assert blocks[0]["description"] == "Windows 11 Pro"


def test_listing_keeps_identifiers_and_marks_firmware_entries():
    backend = build_backend()

    targets = backend.list_targets()

    by_kind = {target.kind: target for target in targets}
    assert by_kind["os"].identifier == "{cbd971bf-b7b8-4885-951a-fa03044f5d71}"
    assert by_kind["os"].title == "Windows 11 Pro"
    assert by_kind["firmware"].selectable is False
    assert "firmware" in by_kind["firmware"].note


def test_current_default_comes_from_the_boot_manager():
    backend = build_backend()

    current = backend.current_default()

    assert current is not None
    assert current.identifier == "{cbd971bf-b7b8-4885-951a-fa03044f5d71}"


def test_setting_the_default_uses_the_documented_command():
    runner = FakeRunner()
    backend = build_backend(runner=runner)
    target = backend.list_targets()[0]
    runner.reset()

    result = backend.set_default(target)

    assert result.ok
    assert runner.commands == [
        "C:\\Windows\\System32\\bcdedit.exe /default {cbd971bf-b7b8-4885-951a-fa03044f5d71}"
    ]
    assert result.reboot_required is False


def test_one_time_boot_uses_the_boot_sequence():
    runner = FakeRunner()
    backend = build_backend(runner=runner)
    target = backend.list_targets()[0]
    runner.reset()

    result = backend.set_next_boot(target)

    assert result.ok
    assert result.reboot_required
    assert runner.commands == [
        "C:\\Windows\\System32\\bcdedit.exe /bootsequence {cbd971bf-b7b8-4885-951a-fa03044f5d71}"
    ]


def test_firmware_entries_are_refused():
    runner = FakeRunner()
    backend = build_backend(runner=runner)
    firmware = next(target for target in backend.list_targets() if target.kind == "firmware")
    runner.reset()

    result = backend.set_default(firmware)

    assert not result.ok
    assert runner.calls == []


def test_invalid_identifiers_are_refused():
    runner = FakeRunner()
    backend = build_backend(runner=runner)
    target = replace(backend.list_targets()[0], identifier="not-an-identifier")
    runner.reset()

    result = backend.set_default(target)

    assert not result.ok
    assert "not a valid BCD identifier" in result.message
    assert runner.calls == []


def test_administrator_privileges_are_required():
    runner = FakeRunner()
    backend = build_backend(runner=runner, admin=False)

    with pytest.raises(PrivilegeError):
        backend.list_targets()
    with pytest.raises(PrivilegeError):
        backend.current_default()
    assert runner.calls == []


def test_missing_bcdedit_is_reported():
    backend = WindowsBackend(
        runner=FakeRunner(),
        which=lambda name: None,
        is_admin=lambda: True,
    )

    with pytest.raises(BootError) as error:
        backend.list_targets()

    assert "bcdedit was not found" in str(error.value)


def test_failed_command_is_reported():
    runner = FakeRunner(
        {
            "/enum OSLOADER": completed(
                ["bcdedit", "/enum", "OSLOADER"], returncode=1, stderr="access denied"
            )
        }
    )
    backend = WindowsBackend(
        runner=runner,
        which=lambda name: "bcdedit",
        is_admin=lambda: True,
    )

    with pytest.raises(BootError) as error:
        backend.list_targets()

    assert "access denied" in str(error.value)


def test_dry_run_does_not_run_bcdedit():
    runner = FakeRunner()
    backend = build_backend(runner=runner)
    target = backend.list_targets()[0]
    runner.reset()

    result = backend.set_default(target, dry_run=True)

    assert result.ok
    assert runner.calls == []
