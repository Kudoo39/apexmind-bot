#!/usr/bin/env python3
"""
ApexMind — scheduled-run entry point.

Designed to be triggered by Task Scheduler / cron. Two modes:

  python run_analysis.py            # PREPARE mode (default, safe)
      Scans Polymarket and writes data/briefing_latest.md, then stops.
      You open the configured coding agent afterwards and reason over the briefing.

  python run_analysis.py --auto     # AUTONOMOUS mode
      Same scan, then invokes Claude Code or Codex non-interactively so the full
      Supervisor→Specialist→record loop runs unattended. Select the CLI with
      APEX_AGENT_PROVIDER (`claude` by default, or `codex`).

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


# The instruction handed to the configured headless coding agent in --auto mode.
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


def agent_command(prompt: str = AUTO_PROMPT) -> tuple[str, list[str]]:
    """Return the configured agent label and non-interactive CLI command."""
    if config.AGENT_PROVIDER == "claude":
        return "Claude Code", [config.CLAUDE_CLI, "-p", prompt]
    if config.AGENT_PROVIDER == "codex":
        return "Codex", [
            config.CODEX_CLI,
            "--ask-for-approval", "never",
            "--search",
            "exec",
            "--sandbox", "workspace-write",
            prompt,
        ]
    raise ValueError(
        f"Unsupported APEX_AGENT_PROVIDER={config.AGENT_PROVIDER!r}; "
        "expected 'claude' or 'codex'."
    )


def autonomous() -> int:
    short = prepare()
    try:
        label, cmd = agent_command()
    except ValueError as exc:
        sys.exit(f"  {exc}")
    print(f"\n  --auto: invoking {label} headlessly…")
    try:
        result = subprocess.run(cmd, cwd=str(config.ROOT), text=True,
                                capture_output=True, timeout=1800)
    except FileNotFoundError:
        cli_var = "APEX_CODEX_CLI" if config.AGENT_PROVIDER == "codex" \
            else "APEX_CLAUDE_CLI"
        sys.exit(f"  {label} CLI not found ('{cmd[0]}'). "
                 f"Set {cli_var} or run without --auto.")
    except subprocess.TimeoutExpired:
        sys.exit(f"  {label} run timed out.")

    # Persist the transcript for the audit trail.
    out_file = config.LOGS_DIR / f"auto-run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.log"
    out_file.write_text((result.stdout or "") + "\n--- STDERR ---\n" +
                        (result.stderr or ""), encoding="utf-8")
    logger.log_event("auto_run", {"provider": config.AGENT_PROVIDER,
                                  "returncode": result.returncode,
                                  "log": str(out_file)})
    print(f"  {label} exited {result.returncode}; transcript -> {out_file}")
    return len(short)


def main() -> None:
    ap = argparse.ArgumentParser(description="ApexMind scheduled analysis run.")
    ap.add_argument("--auto", action="store_true",
                    help="after scanning, drive the configured agent headlessly")
    args = ap.parse_args()
    if args.auto:
        autonomous()
    else:
        prepare()
        print(f"\nDone (PREPARE mode). Open {config.agent_label()} and reason over "
              "the briefing,")
        print("or re-run with --auto for unattended analysis.")


if __name__ == "__main__":
    main()
