"""Tests for the command line interface and the interactive menu."""

from __future__ import annotations

import pytest

from os_flip import __version__, cli, ui
from os_flip.model import BootTarget, Capabilities, OperationResult


class FakeBackend:
    """Backend double that records what the command line asked for."""

    supports_os_prober = True

    def __init__(self):
        self.platform = "Linux"
        self.calls = []
        self.default = BootTarget(
            title="Ubuntu", identifier="gnulinux-simple-1", kind="os", source="test"
        )
        self.targets = [
            self.default,
            BootTarget(
                title="Ubuntu",
                identifier="gnulinux-simple-2",
                kind="os",
                source="test",
            ),
        ]

    def capabilities(self):
        return Capabilities()

    def list_targets(self):
        return list(self.targets)

    def current_default(self):
        return self.default

    def set_default(self, target, dry_run=False):
        self.calls.append(("set_default", target.identifier, dry_run))
        message = (
            f"would set the permanent default to {target.display_path}"
            if dry_run
            else f"permanent default set to {target.display_path}"
        )
        return OperationResult(
            True,
            message,
            details=("identifier used by GRUB: " + (target.identifier or ""),),
        )

    def set_next_boot(self, target, dry_run=False):
        self.calls.append(("set_next_boot", target.identifier, dry_run))
        message = (
            f"would boot {target.display_path} once"
            if dry_run
            else f"one time boot set to {target.display_path}"
        )
        return OperationResult(
            True,
            message,
            details=("ran: grub-reboot " + (target.identifier or ""),),
            reboot_required=True,
        )

    def enable_os_prober(self, dry_run=False):
        self.calls.append(("enable_os_prober", None, dry_run))
        return OperationResult(True, "os-prober enabled")


@pytest.fixture
def backend(monkeypatch):
    instance = FakeBackend()
    monkeypatch.setattr(cli, "get_backend", lambda: instance)
    return instance


def test_help_is_available(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])

    assert exit_info.value.code == 0
    assert "--set-default" in capsys.readouterr().out


def test_version_is_available(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])

    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_list_prints_the_targets(backend, capsys):
    exit_code = cli.main(["--list"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Ubuntu" in output
    assert "gnulinux-simple-1" in output
    assert backend.calls == []


def test_dry_run_explains_the_change_without_changing_anything(backend, capsys):
    exit_code = cli.main(["--set-default", "gnulinux-simple-1", "--dry-run"])

    assert exit_code == 0
    assert [call[2] for call in backend.calls] == [True]
    assert "would set the permanent default" in capsys.readouterr().out


def test_unknown_selector_is_refused(backend, capsys):
    exit_code = cli.main(["--set-default", "does-not-exist"])

    assert exit_code == 1
    assert "not a known boot target" in capsys.readouterr().err
    assert backend.calls == []


def test_ambiguous_title_is_refused(backend, capsys):
    exit_code = cli.main(["--set-default", "Ubuntu"])

    assert exit_code == 1
    assert "matches 2 boot targets" in capsys.readouterr().err
    assert backend.calls == []


def test_selection_by_number_works(backend):
    exit_code = cli.main(["--set-default", "2", "--dry-run"])

    assert exit_code == 0
    assert backend.calls[0][1] == "gnulinux-simple-2"


def test_reboot_flag_without_an_operation_is_a_usage_error(backend, capsys):
    exit_code = cli.main(["--reboot"])

    assert exit_code == 2
    assert "--reboot belongs to" in capsys.readouterr().err


def test_one_time_boot_does_not_reboot_without_consent(backend, monkeypatch):
    called = []
    monkeypatch.setattr(
        cli.power,
        "request_reboot",
        lambda *args, **kwargs: called.append(True) or OperationResult(True, "reboot requested"),
    )

    exit_code = cli.main(["--next-boot", "gnulinux-simple-1"])

    assert exit_code == 0
    assert called == []


def test_reboot_flag_triggers_the_reboot_request(backend, monkeypatch):
    called = []
    monkeypatch.setattr(
        cli.power,
        "request_reboot",
        lambda *args, **kwargs: called.append(True) or OperationResult(True, "reboot requested"),
    )
    monkeypatch.setattr(cli, "_is_privileged", lambda backend: True)

    exit_code = cli.main(["--next-boot", "gnulinux-simple-1", "--reboot"])

    assert exit_code == 0
    assert called == [True]


def test_menu_asks_before_it_changes_anything(backend, monkeypatch, capsys):
    answers = iter(["1", "1", "n", "4"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    exit_code = cli.main([])

    assert exit_code == 0
    assert [call[2] for call in backend.calls] == [True]
    assert "cancelled, nothing was changed" in capsys.readouterr().out


def test_menu_reboots_only_after_an_explicit_confirmation(backend, monkeypatch):
    answers = iter(["2", "1", "y", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    called = []
    monkeypatch.setattr(
        cli.power,
        "request_reboot",
        lambda *args, **kwargs: called.append(True) or OperationResult(True, "reboot requested"),
    )
    monkeypatch.setattr(cli, "_is_privileged", lambda backend: True)

    exit_code = cli.main([])

    assert exit_code == 0
    assert called == [True]


def test_menu_can_be_cancelled_with_a_keyboard_interrupt(backend, monkeypatch, capsys):
    def interrupt(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)

    exit_code = cli.main([])

    assert exit_code == cli.EXIT_CANCELLED
    assert "cancelled" in capsys.readouterr().err


def test_unsupported_platform_is_reported(monkeypatch, capsys):
    from os_flip.model import UnsupportedOperation

    def raise_unsupported():
        raise UnsupportedOperation("OS-Flip supports Linux, Windows and macOS")

    monkeypatch.setattr(cli, "get_backend", raise_unsupported)

    exit_code = cli.main(["--list"])

    assert exit_code == 1
    assert "supports Linux, Windows and macOS" in capsys.readouterr().err


def test_no_color_option_disables_ansi_codes(monkeypatch, capsys):
    instance = FakeBackend()
    monkeypatch.setattr(cli, "get_backend", lambda: instance)
    ui.configure_color(None)

    cli.main(["--list", "--no-color"])

    assert "\033[" not in capsys.readouterr().out
