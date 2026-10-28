"""Tests for the macOS startup disk backend.

systemsetup and bless are mocked, the tests assert the architecture specific
argument lists and never run a native boot command.
"""

from __future__ import annotations

import pytest
from conftest import FakeRunner, completed

from os_flip.backends.macos import (
    APPLE_SILICON,
    INTEL,
    MacOSBackend,
    parse_current_disk,
    parse_startup_disks,
)
from os_flip.model import BootError, PrivilegeError

LIST_OUTPUT = """\
macOS
    /Volumes/Macintosh HD
Windows 11 Pro
    /Volumes/Windows 11 Pro
"""

GET_OUTPUT = "Current startup disk is: Macintosh HD\n"


def build_backend(runner=None, machine="x86_64", root=True, which=None):
    results = {
        "systemsetup -liststartupdisks": completed(
            ["systemsetup", "-liststartupdisks"], stdout=LIST_OUTPUT
        ),
        "systemsetup -getstartupdisk": completed(
            ["systemsetup", "-getstartupdisk"], stdout=GET_OUTPUT
        ),
    }
    if runner is None:
        runner = FakeRunner(results)
    else:
        runner.results.update(results)
    tools = which or {
        "systemsetup": "/usr/sbin/systemsetup",
        "bless": "/usr/sbin/bless",
    }
    return MacOSBackend(
        runner=runner,
        which=lambda name: tools.get(name),
        machine=lambda: machine,
        username="tester",
        is_root=lambda: root,
    )


def test_startup_disks_are_parsed_with_their_path():
    targets = parse_startup_disks(LIST_OUTPUT)

    assert [target.title for target in targets] == ["macOS", "Windows 11 Pro"]
    assert targets[0].identifier == "/Volumes/Macintosh HD"
    assert targets[0].path == ("/Volumes/Macintosh HD",)
    assert targets[0].selectable is True


def test_current_disk_is_parsed():
    assert parse_current_disk(GET_OUTPUT) == "Macintosh HD"


def test_listing_reports_the_startup_disks():
    backend = build_backend()

    targets = backend.list_targets()

    assert len(targets) == 2
    assert targets[1].identifier == "/Volumes/Windows 11 Pro"


def test_current_default_is_reported():
    backend = build_backend()

    current = backend.current_default()

    assert current is not None
    assert current.identifier == "/Volumes/Macintosh HD"


def test_setting_the_startup_disk_uses_the_documented_command():
    runner = FakeRunner()
    backend = build_backend(runner=runner)
    target = backend.list_targets()[0]
    runner.reset()

    result = backend.set_default(target)

    assert result.ok
    assert runner.commands == [
        "/usr/sbin/systemsetup -setstartupdisk /Volumes/Macintosh HD"
    ]


def test_intel_one_time_boot_uses_mount_setboot_nextonly():
    runner = FakeRunner()
    backend = build_backend(runner=runner, machine="x86_64")
    target = backend.list_targets()[0]
    runner.reset()

    result = backend.set_next_boot(target)

    assert result.ok
    assert result.reboot_required
    assert runner.commands == [
        "/usr/sbin/bless --mount /Volumes/Macintosh HD --setBoot --nextonly"
    ]


def test_apple_silicon_one_time_boot_uses_nextonly_with_owner_authorization():
    runner = FakeRunner()
    backend = build_backend(runner=runner, machine="arm64")
    target = backend.list_targets()[0]
    runner.reset()

    result = backend.set_next_boot(target)

    assert result.ok
    assert runner.commands == [
        "/usr/sbin/bless --mount /Volumes/Macintosh HD --nextonly --user tester"
    ]
    assert "--setBoot" not in runner.commands[0]


def test_apple_silicon_reports_the_error_of_the_operating_system():
    runner = FakeRunner(
        {
            "bless": completed(
                ["bless"], returncode=1, stderr="not authorised to set the boot policy"
            )
        }
    )
    backend = build_backend(runner=runner, machine="arm64")
    target = backend.list_targets()[0]
    runner.reset()

    result = backend.set_next_boot(target)

    assert not result.ok
    assert "not authorised" in " ".join(result.details)


def test_missing_bless_is_reported():
    backend = build_backend(which={"systemsetup": "/usr/sbin/systemsetup"})
    target = backend.list_targets()[0]

    result = backend.set_next_boot(target)

    assert not result.ok
    assert "bless was not found" in result.message


def test_missing_systemsetup_is_reported():
    backend = build_backend(which={"bless": "/usr/sbin/bless"})

    with pytest.raises(BootError) as error:
        backend.list_targets()

    assert "systemsetup was not found" in str(error.value)


def test_root_is_required_for_reading_and_writing():
    backend = build_backend(root=False)
    target = parse_startup_disks(LIST_OUTPUT)[0]

    with pytest.raises(PrivilegeError):
        backend.list_targets()
    with pytest.raises(PrivilegeError):
        backend.set_default(target)
    with pytest.raises(PrivilegeError):
        backend.set_next_boot(target)


def test_architecture_detection():
    assert build_backend(machine="arm64").architecture() == APPLE_SILICON
    assert build_backend(machine="x86_64").architecture() == INTEL
