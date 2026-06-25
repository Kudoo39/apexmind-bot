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
    """Render a Decision Memo into a Telegram-HTML message."""
    positions = positions or []
    ts = ts or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    L: list[str] = []
    L.append("🧠 <b>ApexMind — Decision Memo</b>")
    L.append(f"🗓 {_esc(ts)}")
    L.append("")

    if executive_summary:
        L.append("📋 <b>Executive Summary</b>")
        L.append(_esc(executive_summary))
        L.append("")

    if macro_frame:
        L.append("🌍 <b>Macro Frame</b>")
        L.append(_esc(macro_frame))
        L.append("")

    L.append(f"🎯 <b>Positions ({len(positions)})</b>")
    if not positions:
        L.append("No positions today — a disciplined PASS. ✋")
    else:
        for i, p in enumerate(positions, 1):
            q = _esc((p.get("question") or "(unknown market)")[:140])
            ident = _esc(p.get("market_id") or p.get("pred_id") or "?")
            L.append(f"{i}. <b>[{_esc(p.get('direction', '?'))}]</b> {q}")
            L.append(
                f"   id <code>{ident}</code> · model {_pct(p.get('model_prob'))}"
                f" · mkt {_pct(p.get('market_prob'))}"
                f" · edge {_signed_pct(_edge_of(p))}"
                f" · conv {_esc(p.get('conviction', '?'))}/5"
            )
            if p.get("rationale"):
                L.append(f"   ↳ {_esc(str(p['rationale'])[:240])}")
            if p.get("key_uncertainty"):
                L.append(f"   ⚠ {_esc(str(p['key_uncertainty'])[:160])}")
    L.append("")

    L.append("💡 <b>Bet Recommendation</b>")
    if bet_recommendation:
        L.append(_esc(bet_recommendation))
    elif positions:
        for p in positions:
            L.append(
                f"• <b>{_esc(p.get('direction', '?'))}</b> "
                f"{_esc((p.get('question') or '')[:60])} "
                f"— conviction {_esc(p.get('conviction', '?'))}/5"
            )
    else:
        L.append("Stand down — no actionable edge today.")

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
