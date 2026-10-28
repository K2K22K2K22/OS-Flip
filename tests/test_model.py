"""Tests for the shared data model."""

from __future__ import annotations

from os_flip.model import BootTarget, Capabilities, OperationResult


def test_target_display_path_contains_the_submenu_path():
    target = BootTarget(title="Ubuntu", identifier="gnulinux-1", path=("Advanced options",))

    assert target.display_path == "Advanced options > Ubuntu"
    assert target.key == "gnulinux-1"


def test_target_without_identifier_falls_back_to_the_display_path():
    target = BootTarget(title="Legacy")

    assert target.key == "Legacy"


def test_capabilities_explain_missing_operations():
    capabilities = Capabilities(can_set_next_boot=False, limitations=("no one time boot here",))

    assert capabilities.supports("one time boot") is False
    assert "one time boot" in capabilities.reason("one time boot")
    assert capabilities.reason("set default") == ""
    assert capabilities.limitations == ("no one time boot here",)


def test_operation_result_defaults():
    result = OperationResult(True, "done")

    assert result.details == ()
    assert result.reboot_required is False
    assert result.changed_files == ()
