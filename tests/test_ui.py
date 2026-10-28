"""Tests for logging, terminal output and reboot handling."""

from __future__ import annotations

import subprocess

import pytest
from conftest import FakeRunner, completed

from os_flip import power, ui


def test_logging_failure_never_breaks_the_application(tmp_path, monkeypatch, capsys):
    unwritable = tmp_path / "missing-directory" / "os_flip.log"
    monkeypatch.setattr(ui, "log_path", lambda: unwritable)

    assert ui.log("hello") is None
    ui.warning("still working")

    assert "still working" in capsys.readouterr().err


def test_logging_writes_to_the_expected_file(tmp_path, monkeypatch):
    target = tmp_path / "os_flip.log"
    monkeypatch.setattr(ui, "log_path", lambda: target)

    assert ui.log("hello") == target
    assert "hello" in target.read_text()


def test_colors_are_disabled_for_non_interactive_output(monkeypatch, capsys):
    ui.configure_color(False)

    ui.info("plain text")

    assert "\033[" not in capsys.readouterr().out


def test_colors_follow_the_terminal(monkeypatch, capsys):
    ui.configure_color(True)

    ui.info("colored text")

    assert "\033[" in capsys.readouterr().out


def test_reboot_command_matches_the_platform(monkeypatch):
    monkeypatch.setattr(power.platform, "system", lambda: "Linux")
    monkeypatch.setattr(power.shutil, "which", lambda name: "/usr/bin/systemctl")

    assert power.reboot_command() == ["systemctl", "reboot"]


def test_reboot_falls_back_to_shutdown(monkeypatch):
    monkeypatch.setattr(power.platform, "system", lambda: "Linux")
    monkeypatch.setattr(power.shutil, "which", lambda name: None)

    assert power.reboot_command() == ["shutdown", "-r", "now"]


def test_windows_reboot_command(monkeypatch):
    monkeypatch.setattr(power.platform, "system", lambda: "Windows")

    assert power.reboot_command() == ["shutdown", "/r", "/t", "0"]


def test_macos_reboot_command(monkeypatch):
    monkeypatch.setattr(power.platform, "system", lambda: "Darwin")

    assert power.reboot_command() == ["shutdown", "-r", "now"]


def test_reboot_request_is_only_issued_when_asked(monkeypatch):
    monkeypatch.setattr(power.platform, "system", lambda: "Linux")
    monkeypatch.setattr(power.shutil, "which", lambda name: "/usr/bin/systemctl")
    runner = FakeRunner()

    result = power.request_reboot(runner)

    assert result.ok
    assert runner.commands == ["systemctl reboot"]


def test_failed_reboot_request_is_reported(monkeypatch):
    monkeypatch.setattr(power.platform, "system", lambda: "Linux")
    monkeypatch.setattr(power.shutil, "which", lambda name: "/usr/bin/systemctl")
    runner = FakeRunner(
        {"systemctl": completed(["systemctl", "reboot"], returncode=1, stderr="denied")}
    )

    result = power.request_reboot(runner)

    assert not result.ok
    assert "denied" in result.message


def test_the_guard_blocks_a_real_reboot():
    """The guard in conftest must stop any real reboot command."""
    with pytest.raises(AssertionError):
        subprocess.run(["systemctl", "reboot"], check=False)
