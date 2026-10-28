# OS-Flip

Inspect and change the boot selection of a Linux, Windows or macOS machine from
the terminal.

OS-Flip lists the boot targets that the installed boot manager knows about,
shows which one is the current default, and changes either the permanent
default or the selection for the next boot only. Opening OS-Flip never changes
the machine.

## Supported platforms and capabilities

| Capability | Linux (GRUB2) | Windows (BCD) | macOS |
| --- | --- | --- | --- |
| List boot targets | `grub.cfg` entries and Boot Loader Specification entries | `bcdedit /enum OSLOADER`, `BOOTAPP` and `FIRMWARE` | `systemsetup -liststartupdisks` |
| Show the current default | `GRUB_DEFAULT` as number, identifier or `saved`, plus `saved_entry` in the GRUB environment | the `default` element of the boot manager | `systemsetup -getstartupdisk` |
| Change the permanent default | rewrite of `GRUB_DEFAULT` with a GRUB regeneration, or `grub-set-default` when the configuration uses `saved` | `bcdedit /default <id>` | `systemsetup -setstartupdisk <path>` |
| Boot once at the next reboot | `grub-reboot` or `grub2-reboot`, without a regeneration | `bcdedit /bootsequence <id>` | `bless --mount <volume>` with `--setBoot --nextonly` on Intel EFI, with `--nextonly --user <name>` on Apple Silicon |
| Firmware entries | not applicable | listed for orientation, never selected | not applicable |
| Privileges for reading | usually none | an elevated Administrator shell, BCDEdit needs it even to read the store | root, `systemsetup` needs it |
| Privileges for writing | root | an elevated Administrator shell | root |
| Reboot | never automatic, explicit consent only | never automatic, explicit consent only | never automatic, explicit consent only |

Every backend reports the operations it supports, the privileges it needs and
the limits of the operating system. An operation that is not available is
disabled with an explanation, the other capabilities keep working.

Documentation used for the platform behaviour:

* GRUB: [grub-reboot](https://www.gnu.org/software/grub/manual/grub/grub.html#top_002d_002d-reboot),
  [grub-set-default](https://www.gnu.org/software/grub/manual/grub/grub.html#top_002d_002d-set_002ddefault)
  and the `menuentry` and `submenu` commands of the GRUB manual.
* Windows: [bcdedit /enum](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/bcdedit--enum),
  [bcdedit /default](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/bcdedit--default),
  [bcdedit /bootsequence](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/bcdedit--bootsequence).
* macOS: [systemsetup](https://support.apple.com/guide/terminal/change-a-remote-computers-startup-disk-apd0445a31b-28d0-4ad2-bc19-abf64f36446b/mac)
  and the `bless(8)` manual page of macOS.

## Installation

Python 3.9 or newer is required. OS-Flip has no runtime dependencies.

The recommended installation keeps the tool out of the system Python:

```bash
pipx install os-flip
```

A virtual environment works as well:

```bash
python3 -m venv ~/.venvs/os-flip
~/.venvs/os-flip/bin/pip install os-flip
~/.venvs/os-flip/bin/os-flip --list
```

Do not install the package into the system Python with `sudo pip`. OS-Flip only
needs elevated rights for the commands that change the boot selection, and
those can be run with `sudo` at that moment.

## Usage

Without arguments OS-Flip opens its menu in the terminal that started it:

```bash
os-flip
```

Read only listing of the available boot targets:

```bash
os-flip --list
```

Change the permanent default, the target is a number from the listing or a
stable identifier:

```bash
sudo os-flip --set-default 2
sudo os-flip --set-default gnulinux-simple-6b1c2d3e
```

Boot an operating system once, the permanent default stays untouched:

```bash
sudo os-flip --next-boot 2
```

Show what a command would do without touching the machine:

```bash
os-flip --set-default 2 --dry-run
```

Reboot after a successful selection, the explicit flag is required for
non-interactive use:

```bash
sudo os-flip --next-boot 2 --reboot
```

Enable os-prober on Linux. This is the only operation that adds other
operating systems to the menu, it is never done automatically:

```bash
sudo os-flip --enable-os-prober
```

Further options: `--help` for the full list, `--version` for the version and
`--no-color` for output without escape sequences.

## How entries are identified

On Linux the entries of `grub.cfg` are parsed with their stable identifier,
which GRUB generates as `gnulinux-simple-<uuid>` or as
`gnulinux-<kernel>-<uuid>`. Entries inside a submenu keep the path of the
submenu, so the command that GRUB receives looks like
`gnulinux-advanced-<uuid>>gnulinux-6.8.0-31-generic-<uuid>`. Entries without
an identifier are listed but can only be selected by their number.

On Windows the identifier of the Boot Configuration Data entry is used, for
example `{cbd971bf-b7b8-4885-951a-fa03044f5d71}`. Firmware entries are listed
with the note that they cannot be selected by OS-Flip.

On macOS the startup disk path reported by `systemsetup` is used.

An ambiguous selection is refused. OS-Flip never picks the first entry that
looks similar.

## Backups and recovery

Before OS-Flip changes `/etc/default/grub` it writes a backup next to the
file, with a timestamp and a random suffix so that two operations in the same
second cannot overwrite each other:

```text
/etc/default/grub.bak.20261029T012500Z.a1b2c3
```

The new configuration is written to a temporary file in the same directory and
moved into place in one step, so an interrupted write cannot leave a half
written file behind. Comments, other settings and the permissions of the file
are kept.

When the GRUB regeneration fails, the previous configuration is restored and
the output says whether the restore worked. The backup file is named in the
message, and it can be copied back by hand:

```bash
sudo cp /etc/default/grub.bak.20261029T012500Z.a1b2c3 /etc/default/grub
sudo update-grub
```

A failed regeneration can leave the generated boot configuration in an unclear
state. The output names the backup and the regeneration command so that both
can be checked. OS-Flip does not claim that everything was rolled back.

## Logging

OS-Flip writes an activity log. As root the log is `/var/log/os_flip.log`,
for every other user it is `/tmp/os_flip_<user>.log`. If the log file cannot
be written, the tool prints a warning and keeps working.

## Tests

```bash
python -m pip install -e ".[dev]"
python -m pytest
ruff check .
```

The test suite is safe to run anywhere. It works on temporary directories and
mocked commands, it never changes the boot configuration of the host and it
never reboots, shuts down or restarts the machine. The test guard fails any
test that tries to run a reboot or shutdown command.

The continuous integration runs the suite on Linux, Windows and macOS with
Python 3.9, 3.12 and 3.14. Behaviour that depends on real hardware, for
example a firmware boot menu or the Startup Security Utility of a specific
Mac, is not verified by the continuous integration and is reported as
unverified.

## Known limitations

* Linux support covers GRUB2 with generated configuration files. Other
  bootloaders are not supported.
* The Linux backend needs `update-grub`, `grub-mkconfig` or `grub2-mkconfig`
  for a permanent default and `grub-reboot` or `grub2-reboot` for a one time
  boot. Without them the matching operation is disabled.
* Windows firmware entries cannot be selected, the firmware boot order stays
  untouched.
* On Apple Silicon the Startup Security Utility decides which systems may be
  started. OS-Flip reports the error of the operating system and never
  changes a security policy.
* macOS needs root for `systemsetup` and `bless`, the password prompt belongs
  to `sudo`.
* OS-Flip never reboots on its own. A reboot needs an explicit confirmation
  in the menu or the `--reboot` flag.

## License

MIT, see [LICENSE](LICENSE). The original author is
[AK](https://github.com/AKris15), the attribution is part of the license.
