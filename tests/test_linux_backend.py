"""Tests for the Linux and GRUB2 backend.

Every test works on temporary files and a fake subprocess runner, so no test
can change the boot configuration of the host.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import FakeRunner, completed, load_fixture

from os_flip.backends.linux import LinuxBackend, atomic_write
from os_flip.model import AmbiguousTarget, BootError, PrivilegeError

DEFAULT_CONFIG = """\
# If you change this file, run 'update-grub' afterwards to update
# /boot/grub/grub.cfg.
GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_DISTRIBUTOR="Ubuntu  24.04.1 LTS"
"""


def build_backend(tmp_path: Path, runner=None, which=None, root=True, config_text=DEFAULT_CONFIG):
    default_config = tmp_path / "etc-default-grub"
    default_config.write_text(config_text, encoding="utf-8")
    grub_cfg = tmp_path / "grub.cfg"
    grub_cfg.write_text(load_fixture("debian.cfg"), encoding="utf-8")
    tools = (
        which
        if which is not None
        else {"update-grub": "/usr/sbin/update-grub", "grub-reboot": "/usr/sbin/grub-reboot"}
    )
    backend = LinuxBackend(
        default_config=default_config,
        grub_config=grub_cfg,
        grubenv=tmp_path / "grubenv",
        bls_directory=tmp_path / "entries",
        runner=runner or FakeRunner(),
        which=lambda name: tools.get(name),
        is_root=lambda: root,
    )
    return backend, default_config, grub_cfg


def test_listing_is_read_only(tmp_path):
    backend, default_config, grub_cfg = build_backend(tmp_path)
    before = (default_config.read_text(), grub_cfg.read_text())

    targets = backend.list_targets()

    assert [target.title for target in targets][0] == "Ubuntu 24.04 LTS"
    assert (default_config.read_text(), grub_cfg.read_text()) == before
    assert not list(tmp_path.glob("*.bak.*"))


def test_duplicate_titles_are_listed_and_resolved_by_identifier(tmp_path):
    backend, _, grub_cfg = build_backend(tmp_path)
    grub_cfg.write_text(load_fixture("duplicate_titles.cfg"), encoding="utf-8")

    targets = backend.list_targets()

    assert len(targets) == 2
    resolved = backend._resolve("gnulinux-simple-bbbb")
    assert resolved is not None
    assert resolved.identifier == "gnulinux-simple-bbbb"


def test_ambiguous_title_is_refused(tmp_path):
    backend, _, grub_cfg = build_backend(tmp_path)
    grub_cfg.write_text(load_fixture("duplicate_titles.cfg"), encoding="utf-8")

    with pytest.raises(AmbiguousTarget):
        backend._resolve("Linux")


def test_numeric_default_is_resolved(tmp_path):
    backend, _, _ = build_backend(tmp_path)

    current = backend.current_default()

    assert current is not None
    assert current.identifier == "gnulinux-simple-6b1c2d3e"


def test_named_default_is_resolved(tmp_path):
    backend, _, _ = build_backend(tmp_path, config_text='GRUB_DEFAULT="gnulinux-simple-6b1c2d3e"\n')

    current = backend.current_default()

    assert current is not None
    assert current.title == "Ubuntu 24.04 LTS"


def test_saved_default_is_read_from_the_grub_environment(tmp_path):
    runner = FakeRunner(
        {
            "grub-editenv list": completed(
                ["grub-editenv", "list"], stdout="saved_entry=gnulinux-6.8.0-31-generic-6b1c2d3e\n"
            )
        }
    )
    tools = {"grub-editenv": "/usr/sbin/grub-editenv"}
    backend, _, _ = build_backend(tmp_path, runner=runner, which=tools, config_text="GRUB_DEFAULT=saved\n")

    current = backend.current_default()

    assert current is not None
    assert current.title.endswith("6.8.0-31-generic")
    assert runner.commands == ["/usr/sbin/grub-editenv list"]


def test_dry_run_does_not_change_anything(tmp_path):
    runner = FakeRunner()
    backend, default_config, _ = build_backend(tmp_path, runner=runner)
    target = backend.list_targets()[0]

    result = backend.set_default(target, dry_run=True)

    assert result.ok
    assert default_config.read_text() == DEFAULT_CONFIG
    assert runner.calls == []


def test_setting_the_default_keeps_comments_and_backs_up_the_file(tmp_path):
    runner = FakeRunner()
    backend, default_config, _ = build_backend(tmp_path, runner=runner)
    target = next(t for t in backend.list_targets() if t.identifier == "gnulinux-simple-6b1c2d3e")

    result = backend.set_default(target)

    assert result.ok
    content = default_config.read_text()
    assert 'GRUB_DEFAULT="gnulinux-simple-6b1c2d3e"' in content
    assert "# If you change this file" in content
    assert 'GRUB_DISTRIBUTOR="Ubuntu  24.04.1 LTS"' in content
    assert runner.commands == ["update-grub"]
    backups = list(tmp_path.glob("etc-default-grub.bak.*"))
    assert len(backups) == 1
    assert "GRUB_DEFAULT=0" in backups[0].read_text()


def test_backups_never_collide(tmp_path):
    backend, default_config, _ = build_backend(tmp_path)

    first = backend._backup(default_config)
    second = backend._backup(default_config)

    assert first != second


def test_failed_regeneration_restores_the_previous_configuration(tmp_path):
    runner = FakeRunner({"update-grub": completed(["update-grub"], returncode=1, stderr="broken")})
    backend, default_config, _ = build_backend(tmp_path, runner=runner)
    target = backend.list_targets()[0]

    result = backend.set_default(target)

    assert not result.ok
    assert "restored" in result.message
    assert default_config.read_text() == DEFAULT_CONFIG
    assert any("restore: succeeded" in detail for detail in result.details)


def test_missing_regeneration_command_refuses_to_change_anything(tmp_path):
    backend, default_config, _ = build_backend(tmp_path, which={})

    result = backend.set_default(backend.list_targets()[0])

    assert not result.ok
    assert "regeneration command" in result.message
    assert default_config.read_text() == DEFAULT_CONFIG


def test_writing_the_default_needs_root(tmp_path):
    backend, default_config, _ = build_backend(tmp_path, root=False)

    with pytest.raises(PrivilegeError):
        backend.set_default(backend.list_targets()[0])

    assert default_config.read_text() == DEFAULT_CONFIG


def test_one_time_boot_uses_grub_reboot_without_regenerating(tmp_path):
    runner = FakeRunner()
    tools = {"grub-reboot": "/usr/sbin/grub-reboot"}
    backend, _, _ = build_backend(tmp_path, runner=runner, which=tools)
    target = next(t for t in backend.list_targets() if t.title.endswith("6.8.0-31-generic"))

    result = backend.set_next_boot(target)

    assert result.ok
    assert result.reboot_required
    assert runner.commands == [
        "/usr/sbin/grub-reboot gnulinux-advanced-6b1c2d3e>gnulinux-6.8.0-31-generic-6b1c2d3e"
    ]


def test_one_time_boot_reports_a_missing_tool(tmp_path):
    backend, _, _ = build_backend(tmp_path, which={})

    result = backend.set_next_boot(backend.list_targets()[0])

    assert not result.ok
    assert "grub-reboot" in result.message


def test_enabling_os_prober_is_an_explicit_operation(tmp_path):
    runner = FakeRunner()
    tools = {"update-grub": "/usr/sbin/update-grub", "os-prober": "/usr/bin/os-prober"}
    backend, default_config, _ = build_backend(tmp_path, runner=runner, which=tools)

    result = backend.enable_os_prober()

    assert result.ok
    assert 'GRUB_DISABLE_OS_PROBER="false"' in default_config.read_text()
    assert runner.commands == ["update-grub"]


def test_enabling_os_prober_reports_a_missing_tool(tmp_path):
    backend, _, _ = build_backend(tmp_path, which={})

    result = backend.enable_os_prober()

    assert not result.ok
    assert "os-prober is not installed" in result.message


def test_missing_grub_configuration_is_explained(tmp_path):
    default_config = tmp_path / "grub-default"
    default_config.write_text("GRUB_DEFAULT=0\n", encoding="utf-8")
    backend = LinuxBackend(
        default_config=default_config,
        grub_config=tmp_path / "missing.cfg",
        runner=FakeRunner(),
        which=lambda name: None,
        is_root=lambda: True,
    )

    with pytest.raises(BootError) as error:
        backend.list_targets()

    assert "no GRUB configuration" in str(error.value)


def test_atomic_write_keeps_the_mode_of_the_file(tmp_path):
    target = tmp_path / "grub-default"
    target.write_text("old\n", encoding="utf-8")
    os.chmod(target, 0o640)

    atomic_write(target, "new\n")

    assert target.read_text() == "new\n"
    assert target.stat().st_mode & 0o777 == 0o640


def test_listing_never_runs_a_command(tmp_path):
    runner = FakeRunner()
    backend, _, _ = build_backend(tmp_path, runner=runner)

    backend.list_targets()

    assert runner.calls == []


def test_regeneration_command_uses_update_grub(tmp_path):
    backend, _, grub_cfg = build_backend(tmp_path)

    assert backend._regeneration_command() == ["update-grub"]
    assert grub_cfg.exists()


def test_regeneration_command_uses_grub2_mkconfig_on_fedora(tmp_path):
    backend, _, grub_cfg = build_backend(
        tmp_path, which={"grub2-mkconfig": "/usr/sbin/grub2-mkconfig"}
    )

    assert backend._regeneration_command() == [
        "grub2-mkconfig",
        "-o",
        str(grub_cfg),
    ]


def test_regeneration_command_uses_grub_mkconfig_on_arch(tmp_path):
    backend, _, grub_cfg = build_backend(tmp_path, which={"grub-mkconfig": "/usr/sbin/grub-mkconfig"})

    assert backend._regeneration_command() == ["grub-mkconfig", "-o", str(grub_cfg)]


def test_boot_loader_entries_are_listed_without_an_identifier(tmp_path):
    backend, _, _ = build_backend(tmp_path)
    entries = backend._bls_directory
    entries.mkdir(parents=True, exist_ok=True)
    (entries / "5d4b2c.conf").write_text(
        "title Fedora Linux\nversion 6.9.5\nlinux /boot/vmlinuz\n", encoding="utf-8"
    )

    targets = backend.list_targets()

    bls = [target for target in targets if target.kind == "bls"]
    assert len(bls) == 1
    assert bls[0].title == "Fedora Linux"
    assert bls[0].selectable is False


def test_boot_loader_entries_with_an_identifier_can_be_selected(tmp_path):
    backend, _, _ = build_backend(tmp_path)
    entries = backend._bls_directory
    entries.mkdir(parents=True, exist_ok=True)
    (entries / "5d4b2c.conf").write_text(
        "title Fedora Linux\nid 8e2f4a1c\nlinux /boot/vmlinuz\n", encoding="utf-8"
    )

    targets = backend.list_targets()

    bls = [target for target in targets if target.kind == "bls"]
    assert bls[0].identifier == "8e2f4a1c"
    assert bls[0].selectable is True


def test_missing_default_setting_leaves_the_current_default_unknown(tmp_path):
    backend, _, _ = build_backend(tmp_path, config_text="GRUB_TIMEOUT=5\n")

    assert backend.current_default() is None


def test_saved_default_is_not_overwritten_when_the_entry_is_unknown(tmp_path):
    runner = FakeRunner(
        {"grub-editenv list": completed(["grub-editenv", "list"], stdout="saved_entry=unknown-id\n")}
    )
    tools = {"grub-editenv": "/usr/sbin/grub-editenv", "grub-set-default": "/usr/sbin/grub-set-default"}
    backend, default_config, _ = build_backend(
        tmp_path, runner=runner, which=tools, config_text="GRUB_DEFAULT=saved\n"
    )

    assert backend.current_default() is None
    assert default_config.read_text() == "GRUB_DEFAULT=saved\n"
