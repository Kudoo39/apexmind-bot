"""
tools/atomic_io.py — all-or-nothing file writes for ApexMind's source-of-truth files.

The track record (`memory/predictions.json`) is hand-edited, git-diffed, and the only
record of the agent's calls — a crash, a full disk, or a kill -9 mid-write must never
truncate it. `Path.write_text()` truncates-then-writes, so an interrupted write leaves
an empty or partial file (the whole book lost).

These helpers serialise to a temp file in the **same directory**, flush+fsync it, then
`os.replace()` it over the target. `os.replace` is an atomic rename on both POSIX and
Windows, so a concurrent reader (or a crash at any instant) ever sees only the intact
old file or the complete new one — never a half-written one. Same dir guarantees the
rename stays on one filesystem (a cross-device rename is not atomic).

This is plumbing only: it never changes *what* is written, just makes the write durable.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path, text: str, encoding: str = "utf-8") -> None:
    """Write `text` to `path` atomically (temp file in the same dir + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)              # atomic rename on POSIX + Windows
    except BaseException:
        # The replace never happened (or the write itself failed): the original target
        # is untouched. Clean up the temp file so a failed write doesn't litter the dir.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, data: Any, *, indent: int = 2,
                      ensure_ascii: bool = False) -> None:
    """Serialise `data` to pretty JSON (git-diffable) and write it atomically."""
    atomic_write_text(path, json.dumps(data, indent=indent, ensure_ascii=ensure_ascii))
