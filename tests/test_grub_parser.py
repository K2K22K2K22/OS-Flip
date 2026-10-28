"""Tests for the focused GRUB configuration parser."""

from __future__ import annotations

from conftest import load_fixture

from os_flip.backends.grub_parser import parse_grub_config


def test_debian_configuration_keeps_ids_and_submenu_paths():
    entries, warnings = parse_grub_config(load_fixture("debian.cfg"), "debian.cfg")

    assert warnings == []
    titles = [entry.title for entry in entries]
    assert "Ubuntu 24.04 LTS" in titles
    assert "Advanced options for Ubuntu 24.04 LTS" in titles

    top = next(entry for entry in entries if entry.title == "Ubuntu 24.04 LTS")
    assert top.entry_id == "gnulinux-simple-6b1c2d3e"
    assert top.path == ()
    assert top.entry_type == "linux"

    submenu = next(entry for entry in entries if entry.is_submenu)
    assert submenu.entry_id == "gnulinux-advanced-6b1c2d3e"

    nested = next(entry for entry in entries if entry.title.endswith("6.8.0-31-generic"))
    assert nested.entry_id == "gnulinux-6.8.0-31-generic-6b1c2d3e"
    assert nested.path == ("Advanced options for Ubuntu 24.04 LTS",)
    assert nested.path_ids == ("gnulinux-advanced-6b1c2d3e",)


def test_fedora_configuration_detects_chainloader_entries():
    entries, _ = parse_grub_config(load_fixture("fedora.cfg"), "fedora.cfg")

    windows = next(entry for entry in entries if entry.title.startswith("Windows Boot Manager"))
    assert windows.entry_id == "osprober-chain-1a2b3c"
    assert windows.entry_type == "chainloader"


def test_double_quoted_titles_are_read():
    entries, warnings = parse_grub_config(load_fixture("arch.cfg"), "arch.cfg")

    assert warnings == []
    assert entries[0].title == "Arch Linux"
    assert entries[0].entry_id == "arch-linux-advanced-77aa"


def test_duplicate_titles_are_kept_as_separate_entries():
    entries, _ = parse_grub_config(load_fixture("duplicate_titles.cfg"), "duplicate.cfg")

    assert len(entries) == 2
    assert {entry.entry_id for entry in entries} == {"gnulinux-simple-aaaa", "gnulinux-simple-bbbb"}


def test_entries_without_identifiers_are_reported_without_an_id():
    entries, _ = parse_grub_config(load_fixture("without_identifiers.cfg"), "without_ids.cfg")

    legacy = next(entry for entry in entries if entry.title == "Legacy entry")
    assert legacy.entry_id is None
    nested = next(entry for entry in entries if entry.title == "Nested legacy entry")
    assert nested.entry_id is None
    assert nested.path == ("Legacy submenu",)


def test_quoting_styles_and_escaped_quotes():
    entries, warnings = parse_grub_config(load_fixture("quotes_and_escapes.cfg"), "quotes.cfg")

    assert warnings == []
    titles = [entry.title for entry in entries]
    assert "Windows 11 Pro (on /dev/sda1)" in titles
    assert "Fedora Linux (Workstation)" in titles
    assert "Don't Panic" in titles
    custom = next(entry for entry in entries if entry.title == "Don't Panic")
    assert custom.entry_id == "custom-dont-panic"


def test_nested_submenus_keep_the_full_path():
    entries, _ = parse_grub_config(load_fixture("nested_submenus.cfg"), "nested.cfg")

    nested = entries[0]
    assert nested.path == (
        "Advanced options for Debian",
        "Advanced options for Debian with additional options",
    )
    assert nested.path_ids == ("gnulinux-advanced-debian", "gnulinux-advanced-nested")
    assert nested.entry_id == "gnulinux-6.1-nested"


def test_unbalanced_configuration_produces_a_diagnostic():
    entries, warnings = parse_grub_config(load_fixture("broken.cfg"), "broken.cfg")

    assert warnings
    assert "never closed" in warnings[0]
    assert entries[0].title == "Unclosed entry"


def test_configuration_without_entries_is_reported_as_empty():
    entries, warnings = parse_grub_config("set default=\"0\"\n", "empty.cfg")

    assert entries == []
    assert warnings == []
