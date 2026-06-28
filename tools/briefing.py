"""
Briefing builder — assembles the context packet that Claude Code reasons over.

The briefing is the single hand-off point between the deterministic Python layer
and the LLM reasoning layer. It bundles: the market shortlist, ApexMind's current
beliefs, its calibration record, recent lessons, and any open positions that may
relate to the new markets.

Output is Markdown written to data/briefing_latest.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import config
from tools import atomic_io, memory_store, portfolio, research


def _fmt_history(h: dict[str, Any] | None) -> str:
    """One-line recent price-history summary, or '' if unavailable."""
    if not h:
        return ""
    trail = " ".join(f"{d}:{p:.2f}" for d, p in h.get("points", []))
    return (
        f"- **price history ({h.get('n', 0)} pts):** "
        f"{h.get('sparkline', '')}  "
        f"{h.get('first'):.2f} → {h.get('last'):.2f} "
        f"({h.get('change'):+.2f})  "
        f"range {h.get('min'):.2f}–{h.get('max'):.2f}\n"
        f"  - trail: {trail}\n"
    )


def _fmt_research(m: dict[str, Any]) -> str:
    """Render attached research snippets (if any) as an indented block."""
    bundle = m.get("research")
    if not bundle:
        return ""
    body = research.format_bundle_md(bundle, web_n=3, x_n=2)
    indented = "\n".join("  " + ln for ln in body.splitlines())
    return f"- **research snippets:**\n{indented}\n"


def _fmt_market(i: int, m: dict[str, Any]) -> str:
    bid, ask = m.get("best_bid"), m.get("best_ask")
    book = ""
    if bid or ask:
        book = f"- **book:** bid {bid:.3f} / ask {ask:.3f}"
        if m.get("spread"):
            book += f" (spread {m['spread']:.3f})"
        book += "\n"
    return (
        f"### {i}. {m['question']}\n"
        f"- **market_id:** `{m['id']}`\n"
        f"- **market_prob (P[YES]):** {m['market_prob']:.1%}\n"
        f"- **liquidity:** ${m['liquidity']:,.0f}   |   "
        f"**volume:** ${m['volume']:,.0f}   |   "
        f"**24h vol:** ${m['volume_24hr']:,.0f}\n"
        + book
        + f"- **resolves in:** {m['days_to_resolution']} days "
        f"(end {m['end_date']})\n"
        + (f"- **resolution source:** {m['resolution_source']}\n"
           if m.get("resolution_source") else "")
        + _fmt_history(m.get("history"))
        + _fmt_research(m)
        + f"- **url:** {m['url']}\n"
        + (f"- **description:** {m['description'][:500]}\n"
           if m.get("description") else "")
    )


def build_briefing(shortlist: list[dict[str, Any]],
                   total_scanned: int) -> str:
    # Surface only memory relevant to today's shortlist categories (+ general items).
    cats = sorted({m.get("category") for m in shortlist if m.get("category")})
    beliefs = memory_store.relevant_beliefs(cats)
    lessons_list = memory_store.relevant_lessons(cats)
    calib = memory_store.load_calibration()
    open_preds = memory_store.open_predictions()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    parts: list[str] = []
    parts.append(f"# ApexMind Briefing — {now}\n")
    parts.append(
        f"_Scanned {total_scanned} binary markets; "
        f"{len(shortlist)} shortlisted for analysis._\n"
    )

    # --- Operating instructions for the session -------------------------- #
    parts.append(
        "## How to use this briefing\n"
        "1. Adopt the **SUPERVISOR** role from `system_prompts.md` and triage "
        "the shortlist below.\n"
        "2. For each selected market, adopt the **SPECIALIST** role and produce "
        "a calibrated `model_prob` + `confidence`. **Research first** — use your "
        "in-session WebSearch/WebFetch tools, or "
        "`python main_agent.py research \"<query>\"`. Any pre-fetched snippets are "
        "shown under each market below.\n"
        "3. **Build an evidence ledger as you research.** For every POSITION (and "
        "ideally every call), record what you read and how it moved you: each entry "
        "is `{query, source_url, source_tier (primary|expert|market|social), "
        "key_finding, likelihood_ratio (>1 → YES, <1 → NO), direction}`. Cite real "
        "URLs you actually opened — never invent sources.\n"
        "4. Apply the decision policy "
        f"(MIN_EDGE={config.MIN_EDGE}, MIN_CONFIDENCE={config.MIN_CONFIDENCE}) "
        "and record each call, **including `prior_prob` and the `evidence` array**, "
        "with:\n"
        "   `python main_agent.py record --file pred.json`  (evidence inline)\n"
        "5. Respect the lessons and calibration bias noted below.\n"
        "6. Write a Supervisor Decision Memo to `data/decision_memo_latest.md`, then "
        "push it to Telegram with:\n"
        "   `python main_agent.py notify --file notify.json`\n"
    )

    # --- Calibration snapshot ------------------------------------------- #
    parts.append("## Calibration snapshot (self-knowledge)\n")
    if calib and calib.get("n"):
        parts.append(
            f"- Resolved predictions: **{calib['n']}**\n"
            f"- Mean Brier: **{calib.get('mean_brier')}** "
            f"(market baseline: {calib.get('market_baseline_brier')}, "
            f"edge vs market: {calib.get('edge_vs_market')})\n"
        )
        if calib.get("buckets"):
            parts.append("\n| band | n | avg_forecast | realised | gap |\n"
                         "|---|---|---|---|---|\n")
            for b in calib["buckets"]:
                parts.append(
                    f"| {b['band']} | {b['n']} | {b['avg_forecast']} | "
                    f"{b['realised_yes_rate']} | {b['gap']} |\n")
            parts.append("\n_A positive `gap` means over-forecasting (too high). "
                         "Correct for it._\n")
    else:
        parts.append("- No resolved predictions yet — calibration unknown. "
                     "Be humble and size conviction conservatively.\n")

    # --- Lessons (relevant to today's categories + general) -------------- #
    cat_note = f" relevant to {', '.join(cats)}" if cats else ""
    parts.append(f"\n## Lessons learned (apply these){cat_note}\n")
    if lessons_list:
        for lsn in lessons_list:
            tag = f"({lsn['category']}) " if lsn.get("category") else ""
            parts.append(f"- {tag}{lsn['text']}\n")
    else:
        parts.append("_No lessons recorded yet._\n")

    # --- Beliefs --------------------------------------------------------- #
    parts.append("\n## Current beliefs (world-model)\n")
    if beliefs:
        for b in beliefs:
            parts.append(
                f"- **[{b.get('confidence', '?')}]** {b.get('statement', '')}"
                + (f"  _(topic: {b['topic']})_" if b.get("topic") else "")
                + "\n")
    else:
        parts.append("_No structural beliefs recorded yet._\n")

    # --- Open positions + portfolio exposure ----------------------------- #
    if open_preds:
        parts.append("\n## Open positions (avoid double-counting / re-check)\n")
        for p in open_preds:
            parts.append(
                f"- `{p['pred_id']}` {p.get('question', '')[:70]} — "
                f"model {p.get('model_prob')}, {p.get('decision')} "
                f"{p.get('direction', '')}\n")
        exposure = portfolio.exposure_report()
        if exposure["n_positions"]:
            parts.append("\n### Portfolio exposure (size new bets against this)\n")
            parts.append(portfolio.format_exposure_md(exposure) + "\n")

    # --- The shortlist --------------------------------------------------- #
    parts.append("\n## Market shortlist\n")
    for i, m in enumerate(shortlist, 1):
        parts.append(_fmt_market(i, m))

    parts.append(
        "\n---\n"
        "### Recording a call (with evidence ledger)\n"
        "Write `pred.json` then `python main_agent.py record --file pred.json`:\n"
        "```json\n"
        "{\n"
        '  "market_id": "<id>", "model_prob": 0.50, "prior_prob": 0.45,\n'
        '  "confidence": 0.62, "decision": "POSITION", "direction": "NO",\n'
        '  "conviction": 4, "rationale": "...", "key_uncertainty": "...",\n'
        '  "half_life": "14d",\n'
        '  "evidence": [\n'
        '    {"query": "bill X floor schedule december", '
        '"source_url": "https://...", "source_tier": "primary",\n'
        '     "key_finding": "only 8 session days left before recess", '
        '"likelihood_ratio": 0.5, "direction": "NO"}\n'
        "  ]\n"
        "}\n"
        "```\n"
        "Then write the Decision Memo to `data/decision_memo_latest.md` and send the "
        "Telegram notification (`python main_agent.py notify ...`).\n"
    )

    return "".join(parts)


def write_briefing(shortlist: list[dict[str, Any]], total_scanned: int) -> str:
    text = build_briefing(shortlist, total_scanned)
    atomic_io.atomic_write_text(config.BRIEFING_FILE, text)
    # Also cache the raw shortlist so `record` can backfill market fields. Atomic so a
    # crash mid-write can't leave a truncated cache that silently mis-backfills rows.
    atomic_io.atomic_write_json(
        config.MARKETS_CACHE,
        {"updated_at": datetime.now(timezone.utc).isoformat(), "shortlist": shortlist})
    return text
