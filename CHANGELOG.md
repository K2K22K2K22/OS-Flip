# Changelog

## 2.0.0

Rework of OS-Flip with platform backends, safe configuration changes and a
test suite.

### Added

* Backend interface with one implementation per platform: GRUB2 on Linux,
  BCDEdit on Windows, `systemsetup` and `bless` on macOS.
* Capability reporting per platform, an unsupported operation is disabled with
  an explanation instead of failing later.
* Command line interface with `--list`, `--set-default`, `--next-boot`,
  `--enable-os-prober`, `--dry-run`, `--reboot`, `--no-color` and `--version`.
* Focused parser for generated GRUB configurations with stable identifiers,
  submenu paths, single and double quoted titles and duplicate titles.
* Resolution of numeric, named and saved GRUB defaults, including the
  `saved_entry` of the GRUB environment.
* Atomic configuration writes with a collision resistant backup and a
  rollback when the GRUB regeneration fails.
* Windows backend that keeps the BCD identifiers, uses `bcdedit /default` and
  `bcdedit /bootsequence`, and lists firmware entries without selecting them.
* macOS backend that uses the startup disks reported by `systemsetup`, and
  architecture specific `bless` arguments for a one time boot.
* Test suite with GRUB configuration fixtures and mocked platform commands,
  a guard that fails any test that tries to reboot or shut down the host.
* Continuous integration on Linux, Windows and macOS, packaging checks and
  Ruff as the linter.

### Changed

* Starting OS-Flip is read only. It no longer enables os-prober, runs
  os-prober, regenerates the GRUB configuration or modifies
  `/etc/default/grub`.
* The menu runs in the terminal that started OS-Flip, no second terminal is
  spawned.
* Listing boot entries does not need root. Only the operations that change
  the boot selection need elevated rights.
* A permanent default uses the identifier or the submenu path of the entry
  instead of its display title.
* A one time boot uses `grub-reboot` and no longer regenerates the GRUB
  configuration.
* A reboot always needs an explicit confirmation, the menu defaults to no and
  the command line needs `--reboot`.
* The package moved to `pyproject.toml`, has no runtime dependencies and
  requires Python 3.9 or newer.

### Removed

* Automatic enabling of os-prober during startup. It is available as the
  explicit `--enable-os-prober` operation.
