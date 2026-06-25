#!/usr/bin/env python3
"""
ApexMind — daily PREPARE + notify wrapper (prepare-only flywheel).

Run once a day by Windows Task Scheduler (via apex_daily.cmd):
  1. auto-resolve : settle any open predictions whose markets have resolved on
                    Polymarket (rebuilds data/reflection_packet.json).
  2. prepare      : scan Polymarket + write data/briefing_latest.md (run_analysis).
  3. notify       : when Telegram is configured, ALWAYS push a one-line "Briefing
                    ready" ping (with the Politics/Geopolitics shortlist count); and
                    if NEW open POSITIONs were recorded since the last run, push a
                    short Decision-Memo summary of them. No-op if Telegram is unset.
  4. stamp        : write data/LAST_PREPARE.txt as a glanceable "ready" marker.

The probability reasoning stays human-in-the-loop: you open Claude Code and run the
SUPERVISOR -> Specialist loop over the fresh briefing. This wrapper never records
predictions or sends decision memos of its own — the "brain" is your in-session
reasoning on the Claude Max subscription, not a headless model.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone

import config
import run_analysis
from tools import memory_store, notification, polymarket

# Force UTF-8 so the Task Scheduler console codepage doesn't mojibake the log.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Geopolitics is bucketed under "Politics" by the keyword classifier, so the Politics
# count already covers the "Politics/Geopolitics" pillar. Economy = the macro pillar.
_POLITICS = "Politics"
_ECONOMY = "Economy"
_STATE = config.DATA_DIR / ".apex_daily_state.json"


def _auto_resolve() -> None:
    """Settle any due predictions from Polymarket (isolated subprocess, non-fatal)."""
    try:
        r = subprocess.run(
            [sys.executable, "main_agent.py", "auto-resolve"],
            cwd=str(config.ROOT), timeout=600,
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.stdout:
            print(r.stdout.rstrip())
        if r.returncode != 0 and r.stderr:
            print(f"  auto-resolve stderr: {r.stderr.rstrip()}")
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"  auto-resolve step failed (non-fatal): {exc}")


def _cat(m: dict) -> str:
    return m.get("category") or polymarket.categorize(m)


def _load_seen() -> set:
    try:
        return set(json.loads(_STATE.read_text(encoding="utf-8")).get("seen_position_ids", []))
    except (OSError, ValueError):
        return set()


def _save_seen(ids: set) -> None:
    try:
        _STATE.write_text(json.dumps({"seen_position_ids": sorted(ids)}, indent=2),
                          encoding="utf-8")
    except OSError:
        pass


def _notify_briefing(short: list) -> None:
    """ALWAYS push the 'Briefing ready' ping when Telegram is configured."""
    pol = sum(1 for m in short if _cat(m) == _POLITICS)
    eco = sum(1 for m in short if _cat(m) == _ECONOMY)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        "📡 <b>ApexMind Daily Briefing ready</b>\n"
        f"🗓 {ts}\n"
        f"🎯 <b>{pol}</b> Politics/Geopolitics market(s) shortlisted "
        f"(of {len(short)} total; {eco} macro/economy).\n"
        "Open Claude Code and run <code>/apexmind</code> over the fresh briefing.\n"
        f"📁 {config.BRIEFING_FILE}"
    )
    if not notification.is_configured():
        print("  Telegram not configured — skipping pings "
              "(set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env to enable).")
        return
    ok, detail = notification.send_message(msg)
    print(f"  Telegram briefing ping: {'sent' if ok else 'FAILED'} ({detail})")


def _notify_new_positions() -> None:
    """Push a short Decision-Memo summary ONLY when new open POSITIONs appeared."""
    if not notification.is_configured():
        return
    open_pos = [p for p in memory_store.load_predictions()
                if p.get("status") == "open" and p.get("decision") == "POSITION"]
    seen = _load_seen()
    new = [p for p in open_pos if p.get("pred_id") not in seen]
    if new:
        msg = notification.build_decision_message(
            executive_summary=f"{len(new)} new POSITION(s) recorded since the last "
                              "daily run — review and watch.",
            positions=new)
        ok, detail = notification.send_message(msg)
        print(f"  Telegram positions ping: {len(new)} new · "
              f"{'sent' if ok else 'FAILED'} ({detail})")
    # Re-baseline so each position only ever pings once (resolved ids harmlessly persist).
    _save_seen(seen | {p.get("pred_id") for p in open_pos})


def main() -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}] ApexMind daily prepare…")
    _auto_resolve()
    short = run_analysis.prepare()
    _notify_briefing(short)
    _notify_new_positions()
    stamp = config.DATA_DIR / "LAST_PREPARE.txt"
    stamp.write_text(
        f"READY {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} — "
        f"{len(short)} markets shortlisted "
        f"({sum(1 for m in short if _cat(m) == _POLITICS)} Politics/Geopolitics). "
        "Open Claude Code and run /apexmind over data/briefing_latest.md.\n",
        encoding="utf-8")
    print(f"  stamped {stamp}")


if __name__ == "__main__":
    main()
