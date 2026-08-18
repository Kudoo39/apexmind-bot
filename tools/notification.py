"""
Telegram notifications for ApexMind.

Sends the Supervisor's Decision Memo to your phone after an analysis run. Pure
`requests` + the Telegram Bot API — no extra SDK. Secrets come from `.env`
(see `.env.example`); if they're missing, every function degrades to a no-op
instead of crashing, so the rest of the pipeline never depends on Telegram.

Messages use Telegram **HTML** parse mode. We keep every HTML tag pair on a
single line, which lets us split long memos safely at newline boundaries
(Telegram caps a message at 4096 chars).
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

import requests

import config

TG_LIMIT = 4096
_CHUNK = 3900  # leave headroom under the hard limit


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def _esc(value: Any) -> str:
    """HTML-escape arbitrary content for Telegram's HTML parse mode."""
    return html.escape("" if value is None else str(value), quote=False)


def _pct(x: Any) -> str:
    try:
        return f"{float(x) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def _signed_pct(x: Any) -> str:
    try:
        return f"{float(x) * 100:+.0f}%"
    except (TypeError, ValueError):
        return "—"


def _edge_of(p: dict[str, Any]) -> Any:
    edge = p.get("edge")
    if edge is None and p.get("model_prob") is not None and p.get("market_prob") is not None:
        edge = round(p["model_prob"] - p["market_prob"], 4)
    return edge


def _clip(value: Any, limit: int) -> str:
    """Compact prose without cutting a word in half."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    clipped = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return f"{clipped or text[:limit - 1]}…"


def _signed_money(value: Any) -> str:
    try:
        return f"${float(value):+.2f}"
    except (TypeError, ValueError):
        return "—"


def _side_prob(p: dict[str, Any], side: str, source: str) -> float | None:
    """Return fair/current probability expressed on the requested contract side."""
    raw = p.get(source)
    if raw is None:
        return None
    yes = float(raw)
    return yes if side == "YES" else 1 - yes


_ACTION_UX = {
    "TAKE_PROFIT": ("🟠", "REVIEW PROFIT", 0),
    "EXIT_REVIEW": ("🔴", "REVIEW EXIT", 1),
    "REVIEW": ("🟠", "MANUAL REVIEW", 2),
    "NEW_CANDIDATE": ("🔵", "REVIEW ENTRY", 3),
    "HOLD": ("🟢", "HOLD", 4),
    "HOLD_NO_ADD": ("🟡", "HOLD · NO ADD", 5),
    "PASS": ("⚪", "NO TRADE", 6),
    "CLOSED_NO_ACTION": ("⚫", "CLOSED · NO ACTION", 7),
}


def _action_note(p: dict[str, Any], action: str) -> str:
    residual = _signed_pct(p.get("residual_edge"))
    gate = _pct(config.MIN_EDGE)
    if action == "TAKE_PROFIT":
        return (f"Review taking profit now; remaining held-side edge {residual} "
                f"is below the {gate} action gate.")
    if action == "EXIT_REVIEW":
        return "Review an exit now; the model no longer favors the held side."
    if action == "REVIEW":
        return "Review manually; current price or model data is incomplete."
    if action == "NEW_CANDIDATE":
        return "Candidate only—check price and size before entering; no order was placed."
    if action == "HOLD":
        return f"Hold; the model still sees {residual} residual edge. Do not add automatically."
    if action == "HOLD_NO_ADD":
        return "Hold the existing position, but do not add; residual edge is below the entry gate."
    if action == "CLOSED_NO_ACTION":
        return "Already closed—do nothing. Re-entry requires a fresh explicit decision."
    return "No trade; edge or confidence does not clear the decision gate."


def _action_summary(positions: list[dict[str, Any]]) -> str:
    actions = [str(p.get("trade_action") or (
        "NEW_CANDIDATE" if p.get("decision") == "POSITION" else "PASS"))
        for p in positions]
    review = sum(a in {"TAKE_PROFIT", "EXIT_REVIEW", "REVIEW"} for a in actions)
    hold = sum(a in {"HOLD", "HOLD_NO_ADD"} for a in actions)
    entry = sum(a == "NEW_CANDIDATE" for a in actions)
    quiet = len(actions) - review - hold - entry
    return f"{review} review now · {hold} hold · {entry} entry candidate · {quiet} no action"


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
def is_configured() -> bool:
    return config.telegram_ready()


def _chunk(text: str, limit: int = _CHUNK) -> list[str]:
    """Split on newline boundaries so HTML tags are never cut mid-tag."""
    chunks: list[str] = []
    cur = ""
    for line in text.split("\n"):
        if len(line) > limit:                       # pathological single line
            line = line[: limit - 1] + "…"
        if cur and len(cur) + 1 + len(line) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = line if not cur else f"{cur}\n{line}"
    if cur:
        chunks.append(cur)
    return chunks


def send_message(text: str, parse_mode: str = "HTML",
                 disable_preview: bool = True) -> tuple[bool, str]:
    """Send (possibly multi-part) text to the configured chat.

    Returns (ok, detail). ok is False if Telegram isn't configured or any part
    failed; detail carries the Telegram error description when present.
    """
    if not config.telegram_ready():
        return False, ("Telegram not configured — set TELEGRAM_BOT_TOKEN and "
                       "TELEGRAM_CHAT_ID in .env (see .env.example).")

    url = f"{config.TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    ok, detail = True, "sent"
    for part in _chunk(text):
        try:
            resp = requests.post(url, timeout=30, data={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": part,
                "parse_mode": parse_mode,
                "disable_web_page_preview": "true" if disable_preview else "false",
            })
            body = resp.json() if resp.content else {}
            if not (resp.ok and body.get("ok")):
                ok = False
                detail = body.get("description") or f"HTTP {resp.status_code}"
        except requests.RequestException as exc:
            ok, detail = False, str(exc)
    return ok, detail


def send_test() -> tuple[bool, str]:
    msg = ("✅ <b>ApexMind Telegram test</b>\n"
           "If you can read this, notifications are wired up correctly.\n"
           f"🗓 {_esc(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))}")
    return send_message(msg)


# --------------------------------------------------------------------------- #
# Decision-memo message
# --------------------------------------------------------------------------- #
def build_decision_message(executive_summary: str = "",
                           macro_frame: str = "",
                           positions: list[dict[str, Any]] | None = None,
                           bet_recommendation: str = "",
                           ts: str | None = None) -> str:
    """Render a mobile-first, action-oriented Decision Memo in Telegram HTML."""
    positions = positions or []
    ts = ts or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    L: list[str] = []
    L.append("🧠 <b>ApexMind · Action Brief</b>")
    L.append(f"🕒 {_esc(ts)}")
    L.append("⚠️ <b>Research only</b> · No trade was placed automatically.")
    L.append("")

    L.append("✅ <b>YOUR NEXT MOVE</b>")
    if bet_recommendation:
        L.append(_esc(bet_recommendation))
    elif positions:
        L.append("Use the action queue below; confirm every execution manually.")
    else:
        L.append("Stand down—no actionable edge today.")
    L.append("")

    L.append(f"🚦 <b>ACTION QUEUE ({len(positions)})</b>")
    L.append(_esc(_action_summary(positions)))
    if not positions:
        L.append("⚪ No positions today—a disciplined pass.")
    else:
        ranked = sorted(
            enumerate(positions),
            key=lambda item: (_ACTION_UX.get(str(item[1].get("trade_action")),
                                            ("⚪", "NO TRADE", 9))[2], item[0]))
        for i, (_original_index, p) in enumerate(ranked, 1):
            action = p.get("trade_action") or (
                "NEW_CANDIDATE" if p.get("decision") == "POSITION" else "PASS")
            icon, label, _priority = _ACTION_UX.get(str(action), ("⚪", str(action), 9))
            question = _esc(_clip(p.get("question") or "(unknown market)", 96))
            url = str(p.get("url") or "")
            if url.startswith(("https://", "http://")):
                question = f'<a href="{html.escape(url, quote=True)}">{question}</a>'
            direction = str(p.get("direction") or "?")
            pred_id = _esc(p.get("pred_id") or p.get("market_id") or "?")
            L.append("")
            L.append(f"{i}. {icon} <b>{_esc(label)}</b> <code>[{_esc(action)}]</code>")
            L.append(f"   <b>{_esc(direction)} · {question}</b>")

            trade = p.get("trade") or {}
            if trade and trade.get("status") == "CLOSED":
                L.append(
                    f"   Closed: {_esc(trade.get('side'))} · entry "
                    f"{_pct(trade.get('entry_price'))} → exit {_pct(trade.get('close_price'))}"
                )
            elif trade:
                side = str(trade.get("side") or direction)
                current = p.get("current_side_price")
                fair = p.get("fair_side_price")
                L.append(
                    f"   Position: {_esc(side)} · now {_pct(current)} · fair {_pct(fair)}"
                    f" · residual {_signed_pct(p.get('residual_edge'))}"
                    f" · P&amp;L {_signed_money(p.get('unrealized_pnl'))}"
                    f" ({_signed_pct(p.get('roi'))})"
                )
            else:
                L.append("   Position: not held")

            L.append(
                f"   Forecast: YES {_pct(p.get('model_prob'))} vs market "
                f"{_pct(p.get('market_prob'))} · edge {_signed_pct(_edge_of(p))}"
                f" · confidence {_pct(p.get('confidence'))}"
                f" · <code>{pred_id}</code>"
            )
            L.append(f"   → <b>{_esc(_action_note(p, str(action)))}</b>")
            if p.get("rationale"):
                L.append(f"   Why: {_esc(_clip(p['rationale'], 100))}")
            if p.get("key_uncertainty"):
                L.append(f"   Watch: {_esc(_clip(p['key_uncertainty'], 90))}")
    L.append("")

    if executive_summary and not bet_recommendation:
        L.append("📋 <b>Desk summary</b>")
        L.append(_esc(_clip(executive_summary, 420)))
        L.append("")

    if macro_frame:
        L.append("🌍 <b>Context</b>")
        L.append(_esc(_clip(macro_frame, 240)))

    return "\n".join(L)


def notify_decision(executive_summary: str = "",
                    macro_frame: str = "",
                    positions: list[dict[str, Any]] | None = None,
                    bet_recommendation: str = "",
                    ts: str | None = None) -> tuple[bool, str]:
    """Build and send a Decision Memo notification."""
    msg = build_decision_message(executive_summary, macro_frame, positions,
                                 bet_recommendation, ts)
    return send_message(msg)
