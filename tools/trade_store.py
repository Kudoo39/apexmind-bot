"""Actual Polymarket trade ledger, separate from prediction calibration.

Predictions remain open until their markets resolve even when the user sells. This
module tracks execution state so a closed trade is not mistaken for live exposure
and a model POSITION is not mislabeled as an instruction to buy again.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

import config
from tools import atomic_io

VALID_SIDES = {"YES", "NO"}
VALID_STATUSES = {"HOLDING", "CLOSED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_trades() -> list[dict[str, Any]]:
    if not config.TRADES_FILE.exists():
        return []
    try:
        data = json.loads(config.TRADES_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    return data.get("trades", [])


def save_trades(trades: list[dict[str, Any]]) -> None:
    atomic_io.atomic_write_json(
        config.TRADES_FILE, {"updated_at": _now(), "trades": trades})


def _prob(name: str, value: Any, *, required: bool = False) -> float | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number in [0, 1]")
    value = float(value)
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be in [0, 1], got {value}")
    return value


def _positive(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return float(value)


def _normalise(raw: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("each trade must be a JSON object")
    previous = previous or {}
    market_id = str(raw.get("market_id") or previous.get("market_id") or "").strip()
    if not market_id:
        raise ValueError("trade market_id is required")
    side = str(raw.get("side") or previous.get("side") or "").upper()
    status = str(raw.get("status") or previous.get("status") or "HOLDING").upper()
    if side not in VALID_SIDES:
        raise ValueError(f"side must be one of {sorted(VALID_SIDES)}, got {side!r}")
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}, got {status!r}")

    entry_price = _prob("entry_price", raw.get("entry_price", previous.get("entry_price")),
                        required=True)
    shares = _positive("shares", raw.get("shares", previous.get("shares")))
    current_price = _prob("current_price", raw.get("current_price",
                                                    previous.get("current_price")))
    close_price = _prob("close_price", raw.get("close_price", previous.get("close_price")))
    if status == "CLOSED" and close_price is None:
        close_price = current_price

    opened_at = raw.get("opened_at") or previous.get("opened_at") or _now()
    closed_at = raw.get("closed_at") or previous.get("closed_at")
    if status == "CLOSED" and not closed_at:
        closed_at = _now()
    if status == "HOLDING":
        closed_at = None

    return {
        "trade_id": previous.get("trade_id"),
        "market_id": market_id,
        "pred_id": raw.get("pred_id", previous.get("pred_id")),
        "question": raw.get("question", previous.get("question", "")),
        "side": side,
        "status": status,
        "entry_price": entry_price,
        "shares": shares,
        "current_price": current_price,
        "close_price": close_price,
        "opened_at": opened_at,
        "closed_at": closed_at,
        "source": raw.get("source", previous.get("source", "manual")),
        "updated_at": _now(),
    }


def _next_id(trades: list[dict[str, Any]]) -> str:
    nums = []
    for trade in trades:
        ident = str(trade.get("trade_id") or "")
        if ident.startswith("t") and ident[1:].isdigit():
            nums.append(int(ident[1:]))
    return f"t{max(nums, default=0) + 1:05d}"


def sync_trades(entries: list[dict[str, Any]], *, replace: bool = False) -> list[dict[str, Any]]:
    """Upsert a user execution snapshot by (market_id, side)."""
    if not isinstance(entries, list):
        raise ValueError("trade snapshot must contain a JSON list")
    trades = [] if replace else load_trades()
    by_key = {(str(t.get("market_id")), str(t.get("side"))): t for t in trades}
    for raw in entries:
        key = (str(raw.get("market_id") or ""), str(raw.get("side") or "").upper())
        previous = by_key.get(key)
        item = _normalise(raw, previous)
        if not item.get("trade_id"):
            item["trade_id"] = _next_id(trades)
        if previous is not None:
            trades[trades.index(previous)] = item
        else:
            trades.append(item)
        by_key[key] = item
    save_trades(trades)
    return trades


def holding_trades() -> list[dict[str, Any]]:
    return [t for t in load_trades() if t.get("status") == "HOLDING"]


def find_trade(pred: dict[str, Any]) -> dict[str, Any] | None:
    pred_id = str(pred.get("pred_id") or "")
    market_id = str(pred.get("market_id") or "")
    matches = [t for t in load_trades()
               if (pred_id and str(t.get("pred_id") or "") == pred_id)
               or (market_id and str(t.get("market_id") or "") == market_id)]
    holdings = [t for t in matches if t.get("status") == "HOLDING"]
    return (holdings or matches or [None])[-1]


def refresh_prices(fetch_market: Callable[[str], dict[str, Any] | None]) -> tuple[int, int]:
    """Refresh current side prices for holdings using a supplied market fetcher."""
    trades = load_trades()
    updated = missing = 0
    for trade in trades:
        if trade.get("status") != "HOLDING":
            continue
        market = fetch_market(str(trade.get("market_id")))
        yes = None if not market else market.get("market_prob")
        if yes is None:
            missing += 1
            continue
        yes = float(yes)
        trade["current_price"] = round(yes if trade.get("side") == "YES" else 1 - yes, 4)
        trade["question"] = market.get("question") or trade.get("question", "")
        trade["updated_at"] = _now()
        updated += 1
    if updated:
        save_trades(trades)
    return updated, missing


def analyse_trade(pred: dict[str, Any], trade: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compute execution metrics and a conservative action; never auto-ADD."""
    trade = trade if trade is not None else find_trade(pred)
    if trade is None:
        action = "NEW_CANDIDATE" if pred.get("decision") == "POSITION" else "PASS"
        return {"trade_action": action, "trade_status": "NOT_HELD"}
    if trade.get("status") == "CLOSED":
        return {"trade_action": "CLOSED_NO_ACTION", "trade_status": "CLOSED",
                "trade": trade}

    side = trade.get("side")
    model_yes = pred.get("model_prob")
    fair = None if model_yes is None else (float(model_yes) if side == "YES"
                                            else 1 - float(model_yes))
    current = trade.get("current_price")
    if current is None and pred.get("market_prob") is not None:
        current = (float(pred["market_prob"]) if side == "YES"
                   else 1 - float(pred["market_prob"]))
    entry = float(trade["entry_price"])
    shares = float(trade["shares"])
    pnl = None if current is None else round((float(current) - entry) * shares, 4)
    roi = None if current is None else (float(current) - entry) / entry
    residual = None if fair is None or current is None else fair - float(current)

    light_target = min(0.99, entry * (1 + config.LIGHT_PROFIT_TARGET))
    if fair is None:
        target = light_target
    elif residual is not None and residual >= config.MIN_EDGE:
        target = max(light_target, fair - config.MIN_EDGE)
    else:
        target = min(light_target, max(entry, fair))
    target = round(min(0.99, max(0.01, target)), 4)

    confidence_ok = (pred.get("confidence") is not None
                     and float(pred["confidence"]) >= config.MIN_CONFIDENCE)
    if residual is None:
        action = "REVIEW"
    elif residual >= config.MIN_EDGE and confidence_ok:
        action = "HOLD"
    elif residual <= 0 and pnl is not None and pnl > 0:
        action = "TAKE_PROFIT"
    elif roi is not None and roi >= config.LIGHT_PROFIT_TARGET:
        action = "TAKE_PROFIT"
    elif residual > 0:
        action = "HOLD_NO_ADD"
    else:
        action = "EXIT_REVIEW"

    return {
        "trade_action": action,
        "trade_status": "HOLDING",
        "trade": trade,
        "fair_side_price": None if fair is None else round(fair, 4),
        "current_side_price": current,
        "residual_edge": None if residual is None else round(residual, 4),
        "unrealized_pnl": pnl,
        "roi": None if roi is None else round(roi, 4),
        "take_profit_price": target,
    }


def decorate_prediction(pred: dict[str, Any]) -> dict[str, Any]:
    out = dict(pred)
    out.update(analyse_trade(pred))
    return out


def snapshot_rows(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_pred = {str(p.get("pred_id")): p for p in predictions}
    by_market = {str(p.get("market_id")): p for p in predictions}
    rows = []
    for trade in load_trades():
        pred = by_pred.get(str(trade.get("pred_id"))) or by_market.get(str(trade.get("market_id")))
        pred = pred or {"pred_id": trade.get("pred_id"), "market_id": trade.get("market_id"),
                        "question": trade.get("question")}
        row = decorate_prediction(pred)
        row["trade"] = trade
        rows.append(row)
    return rows


def ledger_summary() -> dict[str, float | int]:
    """Execution P&L summary; never substitutes for prediction Brier scoring."""
    trades = load_trades()
    holding = [t for t in trades if t.get("status") == "HOLDING"]
    closed = [t for t in trades if t.get("status") == "CLOSED"]
    holding_cost = sum(float(t["entry_price"]) * float(t["shares"]) for t in holding)
    holding_value = sum(float(t.get("current_price") or t["entry_price"])
                        * float(t["shares"]) for t in holding)
    closed_cost = sum(float(t["entry_price"]) * float(t["shares"]) for t in closed)
    closed_proceeds = sum(float(t.get("close_price") or t["entry_price"])
                          * float(t["shares"]) for t in closed)
    return {
        "holding": len(holding),
        "closed": len(closed),
        "holding_cost": round(holding_cost, 4),
        "holding_value": round(holding_value, 4),
        "unrealized_pnl": round(holding_value - holding_cost, 4),
        "realized_pnl": round(closed_proceeds - closed_cost, 4),
    }
