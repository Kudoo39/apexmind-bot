"""
tools/schema_sentinel.py — lightweight upstream-API schema-drift monitor.

Polymarket's Gamma (`/markets`) and CLOB (`/prices-history`) responses are the ground
truth the entire pipeline parses. If a field is renamed or dropped upstream, the
business logic degrades *silently* — empty shortlists, every market filtered out,
unscored backtests — with no exception to notice. This sentinel samples each critical
response shape and, when a required field goes missing, emits ONE throttled Telegram
alert per endpoint per day.

Design contract (do not break it):
  * OFF by default (config.SCHEMA_SENTINEL_ENABLED).
  * Pure monitoring: `check()` NEVER raises into its caller and NEVER mutates the
    payload or touches business logic — any internal error is swallowed.
  * Throttled: at most one alert per endpoint per SCHEMA_SENTINEL_INTERVAL (default
    24h), persisted in data/.schema_sentinel_state.json.
  * Cheap: validates the FIRST representative item only; a no-op when disabled.

`inspect()` is the pure shape-checker (no side effects, no flag gate) used by both the
auto path and the `schema-check` CLI. `check()` is the flag-gated auto-path wrapper.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import config
from tools import logger, notification

# endpoint -> what a healthy response looks like.
#   kind "items"  : payload is a list of dicts (or a single dict) — sample the first.
#   kind "history": payload is a dict with a "history" list of {t, p} points.
#   required      : keys that MUST be present on the representative item.
#   any_of        : groups where at least ONE key must be present (fallback fields).
EXPECTED: dict[str, dict[str, Any]] = {
    "gamma_markets": {
        "kind": "items",
        "required": ["id", "question", "outcomes", "outcomePrices", "clobTokenIds",
                     "endDate"],
        "any_of": [["volumeNum", "volume"], ["liquidityNum", "liquidity"]],
    },
    "resolved_market": {
        "kind": "items",
        "required": ["id", "question", "outcomes", "outcomePrices", "clobTokenIds"],
        "any_of": [["closedTime", "endDate"]],
    },
    "clob_prices_history": {
        "kind": "history",
        "required_top": ["history"],
        "required_point": ["t", "p"],
    },
}


def _first_dict(payload: Any) -> dict | None:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                return item
    return None


def inspect(endpoint: str, payload: Any) -> list[str]:
    """Return human-readable drift issues for `payload` (empty list == healthy).

    Pure: no flag gate, no alerts. An empty list / empty history is treated as
    HEALTHY — a legitimately empty result is not drift, so we never false-alarm on
    zero rows (only on a present-but-reshaped payload).
    """
    spec = EXPECTED.get(endpoint)
    if not spec:
        return []
    issues: list[str] = []

    if spec["kind"] == "items":
        item = _first_dict(payload)
        if item is None:
            return []                       # empty result — nothing to validate
        for f in spec.get("required", []):
            if f not in item:
                issues.append(f"field `{f}` missing/renamed")
        for group in spec.get("any_of", []):
            if not any(g in item for g in group):
                issues.append(f"none of {group} present (all renamed?)")

    elif spec["kind"] == "history":
        if not isinstance(payload, dict):
            return [f"response is {type(payload).__name__}, expected an object"]
        for f in spec.get("required_top", []):
            if f not in payload:
                issues.append(f"top-level `{f}` missing/renamed")
        hist = payload.get("history")
        if isinstance(hist, list) and hist:
            pt = _first_dict(hist)
            if pt is not None:
                for f in spec.get("required_point", []):
                    if f not in pt:
                        issues.append(f"history point field `{f}` missing/renamed")
    return issues


def _state_path():
    return config.DATA_DIR / ".schema_sentinel_state.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def _maybe_alert(endpoint: str, issues: list[str]) -> bool:
    """Send one throttled alert per endpoint. Returns True if an alert fired."""
    state = _load_state()
    now = time.time()
    last = float(state.get(endpoint, {}).get("last_alert_ts", 0) or 0)
    if now - last < config.SCHEMA_SENTINEL_INTERVAL:
        return False                        # already alerted within the window
    detail = "; ".join(issues)
    msg = ("⚠️ <b>ApexMind — schema drift detected</b>\n"
           f"Endpoint: <code>{endpoint}</code>\n"
           f"Issue(s): {detail}\n\n"
           "Upstream Polymarket response shape changed — parsing may be silently "
           "degrading. Verify the field mappings in tools/polymarket.py.")
    # send_message is a no-op (returns False) if Telegram isn't configured; we still
    # print + log + throttle so the drift is recorded exactly once per window.
    try:
        notification.send_message(msg)
    except Exception:                       # noqa: BLE001 — monitoring must not break
        pass
    print(f"[schema-sentinel] DRIFT {endpoint}: {detail}")
    logger.log_event("schema_drift", {"endpoint": endpoint, "issues": issues})
    state[endpoint] = {"last_alert_ts": now,
                       "last_alert_iso": datetime.now(timezone.utc).isoformat(),
                       "issues": issues}
    _save_state(state)
    return True


def check(endpoint: str, payload: Any) -> list[str]:
    """Auto-path hook: validate a freshly-fetched payload and alert (throttled).

    Flag-gated and exception-proof: returns [] and does nothing when disabled, and
    never raises into the caller. Returns the issue list when enabled (handy in tests).
    """
    if not config.SCHEMA_SENTINEL_ENABLED:
        return []
    try:
        issues = inspect(endpoint, payload)
        if issues:
            _maybe_alert(endpoint, issues)
        return issues
    except Exception:                       # noqa: BLE001 — pure monitoring, never break
        return []
