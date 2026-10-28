"""Terminal output and activity logging."""

from __future__ import annotations

import getpass
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, TextIO

RESET = "\033[0m"
COLORS = {
    "green": "\033[32m",
    "cyan": "\033[36m",
    "yellow": "\033[33m",
    "red": "\033[31m",
}

_color_enabled: Optional[bool] = None


def configure_color(enabled: Optional[bool] = None) -> None:
    """Force colors on or off. Without an argument colors follow the terminal."""
    global _color_enabled
    if enabled is None:
        enabled = bool(sys.stdout.isatty()) and not os.environ.get("NO_COLOR")
    _color_enabled = enabled


def color_enabled() -> bool:
    if _color_enabled is None:
        configure_color()
    return bool(_color_enabled)


def _paint(text: str, color: str) -> str:
    if not color_enabled():
        return text
    return f"{COLORS[color]}{text}{RESET}"


def log_path() -> Path:
    """Root writes to the system log, everyone else to a per user file in /tmp."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return Path("/var/log/os_flip.log")
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = str(os.getuid()) if hasattr(os, "getuid") else "unknown"
    return Path(f"/tmp/os_flip_{user}.log")


def log(message: str) -> Optional[Path]:
    """Append a line to the log file. Logging never breaks the application."""
    path = log_path()
    try:
        with path.open("a", encoding="utf-8") as handle:
            stamp = datetime.now().isoformat(timespec="seconds")
            handle.write(f"[{stamp}] {message}\n")
    except OSError:
        return None
    return path


def _emit(prefix: str, color: str, message: str, stream: TextIO) -> None:
    print(f"{_paint(prefix, color)} {message}", file=stream)


def success(message: str) -> None:
    _emit("OK", "green", message, sys.stdout)
    log(f"SUCCESS: {message}")


def info(message: str) -> None:
    _emit("INFO", "cyan", message, sys.stdout)
    log(f"INFO: {message}")


def warning(message: str) -> None:
    _emit("WARNING", "yellow", message, sys.stderr)
    log(f"WARNING: {message}")


def error(message: str) -> None:
    _emit("ERROR", "red", message, sys.stderr)
    log(f"ERROR: {message}")


def banner(platform_name: str) -> None:
    print(
        rf"""{_paint("   ____   _____          ______ _      _____ _____", "cyan")}
  / __ \ / ____|        |  ____| |    |_   _|  __ \
 | |  | | (___    ___   | |__  | |      | | | |__) |
 | |  | |\___ \         |  __| | |      | | |  ___/
 | |__| |____) |        | |    | |____ _| |_| |
  \____/|_____/         |_|    |______|_____|_|

         Welcome to OS FLIP
              By - AK ({platform_name})
"""
    )
