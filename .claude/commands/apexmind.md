---
description: Run a full ApexMind analysis cycle over the latest Polymarket briefing
---

You are running an ApexMind analysis cycle. Follow these steps precisely.

1. Run `python main_agent.py scan` to refresh `data/briefing_latest.md`.
2. Read `system_prompts.md`, `CLAUDE.md`, `data/briefing_latest.md`, and
   `memory/trades.json` when present. Run `python main_agent.py trades --refresh` so
   actual holdings and closed trades are not confused with prediction status.
3. Adopt the **SUPERVISOR** role and triage the shortlist; pick 3–6 markets worth deep analysis. Announce your picks and why you rejected the rest.
4. For each pick, adopt the **SPECIALIST** role. **Research first** — use your in-session WebSearch/WebFetch tools, or `python main_agent.py research "<query>"` (web + X, cached). Then reason explicitly (resolution criteria → base rate → causal model → evidence → red-team → estimate) and output `model_prob`, `confidence`, `key_uncertainty`, `half_life`. Cite what you actually read; if a source is unavailable, lower confidence rather than guessing.
5. Apply the decision policy from `config.py` (`MIN_EDGE`, `MIN_CONFIDENCE`) and decide
   POSITION/PASS with a conviction 1–5. Separately assign the actual-trade action:
   NEW_CANDIDATE, HOLD, HOLD_NO_ADD, TAKE_PROFIT, EXIT_REVIEW, or CLOSED_NO_ACTION.
   Reaching the 5% light-profit target is not enough to sell while residual edge still
   clears MIN_EDGE; closed trades require explicit REENTER analysis.
6. Record EVERY analysed market with `python main_agent.py record '<json>'` (on Windows, `--file pred.json`).
7. Write a Supervisor Decision Memo to `data/decision_memo_latest.md`.
8. Build a `notify.json` (`executive_summary`, `macro_frame`, `bet_recommendation`, and the `pred_ids` you recorded) and run `python main_agent.py notify --file notify.json` to push the memo to Telegram.

Respect `memory/lessons.md` and the calibration gaps in the briefing. Use the 7-day price history in the briefing. Never invent data. PASS is a valid and common outcome.

$ARGUMENTS
