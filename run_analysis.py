#!/usr/bin/env python3
"""
ApexMind — scheduled-run entry point.

Designed to be triggered by Task Scheduler / cron. Two modes:

  python run_analysis.py            # PREPARE mode (default, safe)
      Scans Polymarket and writes data/briefing_latest.md, then stops.
      You open Claude Code afterwards and reason over the briefing.

  python run_analysis.py --auto     # AUTONOMOUS mode
      Same scan, then invokes the Claude Code CLI in headless print mode
      (`claude -p ...`) so the full Supervisor→Specialist→record loop runs
      unattended on your Claude Max subscription (no API key).
      Requires the `claude` CLI on PATH and appropriate tool permissions.

Both modes flag any open predictions whose end_date has passed and point you at
`python main_agent.py auto-resolve` to settle them from Polymarket. In --auto mode
the headless run also writes a Decision Memo and pushes it to Telegram.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone

from dateutil import parser as dateparser

import config
from tools import briefing, logger, memory_store, polymarket, research

# Force UTF-8 stdout so Windows consoles don't choke on unicode in market text.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _flag_due_resolutions() -> list[dict]:
    """Open predictions whose market end_date is in the past."""
    now = datetime.now(timezone.utc)
    due = []
    for p in memory_store.open_predictions():
        ed = p.get("end_date")
        if not ed:
            continue
        try:
            end = dateparser.parse(ed)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            if end < now:
                due.append(p)
        except (ValueError, OverflowError):
            continue
    return due


def prepare() -> list:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}] ApexMind scan…")
    everything = polymarket.scan_markets()
    short = polymarket.shortlist(everything)
    if config.BRIEFING_INCLUDE_HISTORY:
        polymarket.attach_price_history(short)
    if config.BRIEFING_INCLUDE_RESEARCH:
        research.attach_to_markets(short)
    briefing.write_briefing(short, total_scanned=len(everything))
    logger.log_event("scheduled_scan", {"scanned": len(everything),
                                        "shortlisted": len(short)})
    print(f"  briefing -> {config.BRIEFING_FILE} ({len(short)} markets)")

    due = _flag_due_resolutions()
    if due:
        print(f"\n  {len(due)} open prediction(s) are past their end_date.")
        print("  Settle them automatically with:  python main_agent.py auto-resolve")
    return short


# The instruction handed to the headless Claude Code process in --auto mode.
AUTO_PROMPT = """You are ApexMind running unattended. Do the following:
1. Read system_prompts.md and data/briefing_latest.md.
2. Adopt the SUPERVISOR role: set the macro frame, then triage the shortlist and
   pick 3-6 markets.
3. For each, adopt the SPECIALIST role and produce a calibrated model_prob and
   confidence with explicit reasoning. Research first — use your WebSearch/WebFetch
   tools, or run `python main_agent.py research "<query>"` for web + X snippets.
4. Apply the decision policy and record EVERY analysed market by running:
   python main_agent.py record '<json>'
   where the JSON includes: market_id, model_prob, confidence, decision, direction,
   conviction, rationale, key_uncertainty, half_life.
5. Write a Supervisor Decision Memo to data/decision_memo_latest.md.
6. Send the memo to Telegram. Write a notify.json file with keys
   executive_summary, macro_frame, bet_recommendation (and optionally pred_ids),
   then run:  python main_agent.py notify --file notify.json
Be calibrated and decisive. PASS is fine. Do not invent data."""


def autonomous() -> int:
    short = prepare()
    print("\n  --auto: invoking Claude Code headless (claude -p)…")
    cmd = [config.CLAUDE_CLI, "-p", AUTO_PROMPT]
    try:
        result = subprocess.run(cmd, cwd=str(config.ROOT), text=True,
                                capture_output=True, timeout=1800)
    except FileNotFoundError:
        sys.exit(f"  Claude CLI not found ('{config.CLAUDE_CLI}'). "
                 "Set APEX_CLAUDE_CLI or run without --auto.")
    except subprocess.TimeoutExpired:
        sys.exit("  Claude Code run timed out.")

    # Persist the transcript for the audit trail.
    out_file = config.LOGS_DIR / f"auto-run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.log"
    out_file.write_text((result.stdout or "") + "\n--- STDERR ---\n" +
                        (result.stderr or ""), encoding="utf-8")
    logger.log_event("auto_run", {"returncode": result.returncode,
                                  "log": str(out_file)})
    print(f"  Claude Code exited {result.returncode}; transcript -> {out_file}")
    return len(short)


def main() -> None:
    ap = argparse.ArgumentParser(description="ApexMind scheduled analysis run.")
    ap.add_argument("--auto", action="store_true",
                    help="after scanning, drive Claude Code headlessly to analyse")
    args = ap.parse_args()
    if args.auto:
        autonomous()
    else:
        prepare()
        print("\nDone (PREPARE mode). Open Claude Code and reason over the briefing,")
        print("or re-run with --auto for unattended analysis.")


if __name__ == "__main__":
    main()
