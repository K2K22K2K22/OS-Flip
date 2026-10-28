"""Focused parser for GRUB2 menu configuration.

The parser understands the shape of generated configurations: ``menuentry``
and ``submenu`` headers with quoted titles, stable identifiers and nested
blocks. It is not a GRUB script interpreter, and it refuses to guess when a
header cannot be read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADER_PATTERN = re.compile(r"^(menuentry|submenu)\s+(.*)$")

Token = tuple[str, bool]


@dataclass(frozen=True)
class GrubEntry:
    """A menuentry or submenu found in a GRUB configuration."""

    title: str
    entry_id: str | None
    kind: str
    entry_type: str
    path: tuple[str, ...]
    path_ids: tuple[str | None, ...]
    source: str
    line_number: int

    @property
    def is_submenu(self) -> bool:
        return self.kind == "submenu"


def tokenize(text: str) -> list[Token]:
    """Split a command header into tokens, remembering which ones were quoted."""
    tokens: list[Token] = []
    buffer: list[str] = []
    in_quotes = False
    quote = ""
    escaped = False

    for char in text:
        if escaped:
            buffer.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if in_quotes:
            if char == quote:
                in_quotes = False
                tokens.append(("".join(buffer), True))
                buffer = []
            else:
                buffer.append(char)
            continue
        if char in ("'", '"'):
            in_quotes = True
            quote = char
            continue
        if char.isspace():
            if buffer:
                tokens.append(("".join(buffer), False))
                buffer = []
            continue
        buffer.append(char)

    if buffer:
        tokens.append(("".join(buffer), False))
    return tokens


def find_identifier(tokens: list[Token]) -> str | None:
    """Return the stable GRUB identifier of a header, when it carries one."""
    for index, (token, _) in enumerate(tokens):
        if token == "--id" and index + 1 < len(tokens):
            return tokens[index + 1][0] or None
        if token.startswith("--id="):
            return token.split("=", 1)[1] or None
        if token == "$menuentry_id_option" and index + 1 < len(tokens):
            return tokens[index + 1][0] or None
    return None


def brace_delta(line: str) -> int:
    """Count braces that are not inside a quoted string."""
    delta = 0
    in_quotes = False
    quote = ""
    escaped = False

    for char in line:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if in_quotes:
            if char == quote:
                in_quotes = False
            continue
        if char in ("'", '"'):
            in_quotes = True
            quote = char
            continue
        if char == "{":
            delta += 1
        elif char == "}":
            delta -= 1
    return delta


def parse_grub_config(text: str, source: str = "<config>") -> tuple[list[GrubEntry], list[str]]:
    """Parse a GRUB configuration into entries and diagnostics."""
    entries: list[GrubEntry] = []
    warnings: list[str] = []
    stack: list[dict] = []
    depth = 0

    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        header = HEADER_PATTERN.match(stripped)

        if header:
            keyword, remainder = header.group(1), header.group(2)
            tokens = tokenize(remainder)
            title = next((value for value, was_quoted in tokens if was_quoted), None)
            identifier = find_identifier(tokens)

            if title is None:
                warnings.append(f"{source}:{number}: title of {keyword} could not be read")
                title = ""
            if "{" not in remainder:
                warnings.append(
                    f"{source}:{number}: {keyword} without an opening brace was ignored"
                )
                depth += brace_delta(line)
                _close_finished(stack, depth, entries, source)
                continue

            depth += brace_delta(line)
            stack.append(
                {
                    "kind": keyword,
                    "title": title,
                    "identifier": identifier,
                    "depth": depth,
                    "line": number,
                    "entry_type": "unknown",
                    "path": tuple(item["title"] for item in stack if item["kind"] == "submenu"),
                    "path_ids": tuple(
                        item["identifier"] for item in stack if item["kind"] == "submenu"
                    ),
                }
            )
        else:
            depth += brace_delta(line)
            if stack and stack[-1]["kind"] == "menuentry" and stripped:
                command = stripped.split(None, 1)[0]
                if command in ("linux", "linuxefi"):
                    stack[-1]["entry_type"] = "linux"
                elif command == "chainloader":
                    stack[-1]["entry_type"] = "chainloader"

        _close_finished(stack, depth, entries, source)

    while stack:
        block = stack.pop()
        warnings.append(f"{source}:{block['line']}: {block['kind']} was never closed")
        entries.append(_build_entry(block, source))

    return entries, warnings


def _close_finished(stack: list[dict], depth: int, entries: list[GrubEntry], source: str) -> None:
    while stack and depth < stack[-1]["depth"]:
        block = stack.pop()
        entries.append(_build_entry(block, source))


def _build_entry(block: dict, source: str) -> GrubEntry:
    return GrubEntry(
        title=block["title"],
        entry_id=block["identifier"],
        kind=block["kind"],
        entry_type=block["entry_type"],
        path=block["path"],
        path_ids=block["path_ids"],
        source=source,
        line_number=block["line"],
    )
