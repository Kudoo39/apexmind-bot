"""
JSON-lines event logger for ApexMind.

Every meaningful action (scan, briefing build, prediction, resolution, reflection)
is appended to logs/events-YYYY-MM-DD.jsonl. This is the audit trail that lets the
Reflection role reconstruct *why* a decision was made.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import config


def log_event(event_type: str, payload: dict | None = None) -> dict:
    """Append a structured event. Returns the event dict that was written."""
    now = datetime.now(timezone.utc)
    event = {
        "ts": now.isoformat(),
        "type": event_type,
        "payload": payload or {},
    }
    fname = config.LOGS_DIR / f"events-{now.strftime('%Y-%m-%d')}.jsonl"
    with fname.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def read_events(day: str | None = None) -> list[dict]:
    """Read events for a given YYYY-MM-DD (default today)."""
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fname = config.LOGS_DIR / f"events-{day}.jsonl"
    if not fname.exists():
        return []
    out = []
    for line in fname.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
