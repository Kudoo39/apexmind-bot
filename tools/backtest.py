"""
tools/backtest.py — bootstrap calibration and test ApexMind's edge on history.

The full Supervisor/Specialist reasoning lives in Claude Code and can't be replayed
in pure Python. So this harness replays the *mechanical skeleton* of the process
against **real resolved markets**, with no look-ahead:

  1. Pull cleanly-resolved binary markets from the last `days`.
  2. For each, read the crowd's YES price **as it was `lead_days` before resolution**
     (from CLOB price history) — that snapshot is the crowd's forecast at decision
     time. Reading the *settlement* price would be look-ahead; we never do that.
  3. Run a transparent **strategy** (a stand-in for the Specialist) to produce a
     `model_prob` from the snapshot price (± a look-back move for revert/momentum).
  4. Apply the *same* decision gate as live trading (MIN_EDGE, MIN_CONFIDENCE).
  5. Score everything against the realised outcome with `tools/scoring.py`:
     Brier, edge-vs-market, hit-rate, a calibration curve, rolling Brier over time,
     and a frictionless simulated P&L on the POSITIONs.
  6. Distil candidate lessons from the systematic mistakes.

This is a *lower bound* on ApexMind's real edge (the strategies are dumb on purpose).
Its value is twofold: it seeds the calibration intuition on real outcomes, and it
tells you whether a given mechanical rule has any edge before you trust it.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

import config
from tools import polymarket, scoring
# Category detection lives in polymarket (the scanner needs it too); re-exported here.
from tools.polymarket import categorize


# --------------------------------------------------------------------------- #
# Strategies — map (market_prob, prior_price, history) -> model_prob
# A strategy is a mechanical stand-in for the Specialist's estimate.
# --------------------------------------------------------------------------- #
def _clamp(x: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return max(lo, min(hi, x))


def _strat_market(mkt, prior, hist):
    """No edge — model == market. Calibrates the crowd baseline."""
    return mkt


def _strat_shrink(mkt, prior, hist, lam: float = 0.90):
    """Shrink toward 0.5 — bets the crowd is over-confident at the extremes."""
    return _clamp(0.5 + (mkt - 0.5) * lam)


def _strat_steepen(mkt, prior, hist, gamma: float = 1.15):
    """Push away from 0.5 — the favourite-longshot hypothesis."""
    return _clamp(0.5 + (mkt - 0.5) * gamma)


def _strat_revert(mkt, prior, hist, k: float = 0.5):
    """Fade part of the recent move — crowds over-react to recent news."""
    if prior is None:
        return mkt
    return _clamp(mkt - k * (mkt - prior))


def _strat_momentum(mkt, prior, hist, k: float = 0.5):
    """Extend the recent move — the opposite hypothesis to revert."""
    if prior is None:
        return mkt
    return _clamp(mkt + k * (mkt - prior))


STRATEGIES: dict[str, Callable] = {
    "market": _strat_market,
    "shrink": _strat_shrink,
    "steepen": _strat_steepen,
    "revert": _strat_revert,
    "momentum": _strat_momentum,
}


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #
def _confidence(liquidity: float, volume: float, edge_abs: float) -> float:
    """Heuristic stand-in for the Specialist's self-assessed confidence.

    Depth and a larger edge read as more trustworthy. Gamma often leaves
    `liquidityNum` empty on resolved markets, so we fall back to total volume as the
    depth proxy — otherwise the confidence gate would be blind (every market 0).
    """
    depth = max(liquidity or 0.0, (volume or 0.0) * 0.10)
    depth_f = min(depth / 50_000.0, 1.0)
    c = 0.45 + 0.25 * depth_f + 0.15 * min(edge_abs / 0.20, 1.0)
    return round(max(0.30, min(0.90, c)), 3)


_MIN_LEAD_SECS = 1800     # 30 min — anything closer to close is effectively settlement
_MIN_LIFE_SECS = 2 * 3600  # markets that lived < 2h are too brief to "forecast"


def _nearest_at_or_before(hist: list[dict], ts: int) -> dict | None:
    """Latest price point with t <= ts (no look-ahead), or None."""
    le = [h for h in hist if h["t"] <= ts]
    return le[-1] if le else None


def _pnl(p: dict) -> float:
    """Frictionless P&L per $1 staked on a POSITION, filled at the snapshot price."""
    price, outcome = p["market_prob"], p["outcome"]
    if p["direction"] == "YES":
        return outcome / price - 1.0 if price > 0 else 0.0
    return (1 - outcome) / (1 - price) - 1.0 if price < 1 else 0.0


def simulate_market(m: dict, strategy: str, lead_days: int, window_days: int,
                    min_edge: float, min_conf: float) -> dict | None:
    """Produce one pseudo-prediction (same schema as a real recorded prediction).

    The snapshot price is taken at an **adaptive lead**: the later of "70% through
    the market's life" or "`lead_days` before close", floored at 30 min before close.
    This gives every market — multi-month or single-day — a meaningful forward-looking
    price with genuine uncertainty remaining, and no look-ahead.
    """
    # Whole-life history (interval=max — windowed startTs/endTs is capped at ~2wk).
    hist = polymarket.price_history_full(m["yes_token_id"], fidelity_minutes=60)
    if len(hist) < 3:
        return None

    end_ts = hist[-1]["t"]                     # last traded point ≈ resolution
    first_t, last_t = hist[0]["t"], hist[-1]["t"]
    life = last_t - first_t
    if life < _MIN_LIFE_SECS:
        return None

    snap_target = max(first_t + int(0.70 * life), last_t - lead_days * 86_400)
    snap_target = min(snap_target, last_t - _MIN_LEAD_SECS)
    snap = _nearest_at_or_before(hist, snap_target) or hist[0]
    lead_secs = last_t - snap["t"]
    if lead_secs < _MIN_LEAD_SECS:
        return None
    market_prob = _clamp(snap["p"])

    # Prior reference for revert/momentum: the price one look-back window earlier.
    prior_target = snap["t"] - min(window_days * 86_400, int(0.30 * life))
    prior_pt = _nearest_at_or_before(hist, prior_target)
    prior = prior_pt["p"] if (prior_pt and prior_pt["t"] < snap["t"]) else None

    model_prob = _clamp(STRATEGIES[strategy](market_prob, prior, hist))
    edge = round(model_prob - market_prob, 4)
    conf = _confidence(m.get("liquidity", 0.0), m.get("volume", 0.0), abs(edge))
    decision = "POSITION" if (abs(edge) >= min_edge and conf >= min_conf) else "PASS"

    return {
        "pred_id": f"bt-{m['id']}",
        "market_id": m["id"],
        "question": m["question"],
        "market_prob": round(market_prob, 4),
        "model_prob": round(model_prob, 4),
        "edge": edge,
        "confidence": conf,
        "decision": decision,
        "direction": "YES" if model_prob >= market_prob else "NO",
        "conviction": min(5, max(1, int(abs(edge) / 0.05) + 1)),
        "status": "resolved",
        "outcome": int(m["outcome"]),
        "brier": round((model_prob - m["outcome"]) ** 2, 4),
        "end_date": m.get("resolved_at") or m.get("end_date"),
        "snapshot_ts": snap["t"],
        "lead_hours": round(lead_secs / 3600, 1),
        "liquidity": round(m.get("liquidity", 0.0), 0),
        "category": categorize(m),
    }


def replay_historical_markets(days: int = 90,
                              market_ids: list[str] | None = None,
                              limit: int | None = None) -> list[dict]:
    """Fetch the resolved markets to replay — by id list, or the last `days`.

    The `days` path is **category-stratified** (config.BACKTEST_CATEGORIES /
    BACKTEST_CATEGORY_MIN / BACKTEST_CATEGORY_CAPS) so the scored sample reflects the
    high-edge Politics/Economy niches instead of being swamped by the fast-churning
    Sports/Weather/Crypto markets that dominate a flat recency pull. See
    tools/polymarket.fetch_resolved_markets for the sampling logic.
    """
    if market_ids:
        out = []
        for mid in market_ids:
            raw = polymarket.get_market_by_id(str(mid).strip())
            prepped = polymarket.prepare_resolved(raw) if raw else None
            if prepped:
                out.append(prepped)
        return out
    return polymarket.fetch_resolved_markets(
        days=days, limit=limit or config.BACKTEST_MAX_MARKETS,
        categories=config.BACKTEST_CATEGORIES or None,
        per_category_min=config.BACKTEST_CATEGORY_MIN,
        category_caps=config.BACKTEST_CATEGORY_CAPS or None,
        order=config.BACKTEST_ORDER,
        pages=config.BACKTEST_POOL_PAGES)


# --------------------------------------------------------------------------- #
# Aggregation & reporting
# --------------------------------------------------------------------------- #
def _calib_error(buckets: list[dict]) -> float | None:
    """n-weighted mean |gap| across occupied bands. Lower = better calibrated."""
    tot = sum(b["n"] for b in buckets)
    if not tot:
        return None
    return round(sum(b["n"] * abs(b["gap"]) for b in buckets) / tot, 4)


def _iso_week(end_date: str) -> str:
    try:
        from dateutil import parser as dp
        d = dp.parse(end_date)
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    except Exception:  # noqa: BLE001
        return "unknown"


def _rolling_brier(sims: list[dict]) -> list[dict]:
    groups: dict[str, list[float]] = {}
    for s in sims:
        groups.setdefault(_iso_week(s["end_date"]), []).append(s["brier"])
    out = []
    for k in sorted(groups):
        b = groups[k]
        out.append({"period": k, "n": len(b), "brier": round(sum(b) / len(b), 4)})
    return out


def _category_metrics(sims: list[dict]) -> dict:
    """Compact per-group metrics: Brier, edge-vs-market, hit-rate, ROI, sample size."""
    rep = scoring.calibration_report(sims)
    positions = [s for s in sims if s["decision"] == "POSITION"]
    pnls = [_pnl(p) for p in positions]
    won = sum(1 for p in positions if (p["direction"] == "YES") == (p["outcome"] == 1))
    return {
        "n": len(sims),
        "mean_brier": rep.get("mean_brier"),
        "market_baseline_brier": rep.get("market_baseline_brier"),
        "edge_vs_market": rep.get("edge_vs_market"),
        "n_positions": len(positions),
        "hit_rate": round(won / len(positions), 3) if positions else None,
        "roi": round(sum(pnls) / len(pnls), 4) if pnls else None,
    }


def _category_insights(cats: dict, min_n: int = 6) -> list[str]:
    """Plain-language takeaways from the per-category table."""
    insights: list[str] = []
    big = {c: v for c, v in cats.items() if v["n"] >= min_n}
    pool = big or cats

    edged = [(c, v) for c, v in pool.items() if v["edge_vs_market"] is not None]
    if edged:
        c, v = max(edged, key=lambda kv: kv[1]["edge_vs_market"])
        if v["edge_vs_market"] > 0.005:
            insights.append(
                f"Strongest model edge in **{c}**: {v['edge_vs_market']:+.3f} Brier "
                f"vs the crowd over {v['n']} markets — worth deeper Specialist focus.")
        else:
            cw, vw = min(edged, key=lambda kv: kv[1]["edge_vs_market"])
            insights.append(
                f"No category beat the crowd; closest is **{c}** "
                f"({v['edge_vs_market']:+.3f}), weakest is **{cw}** "
                f"({vw['edge_vs_market']:+.3f}) — the crowd is efficient here.")

    brier_ranked = [(c, v) for c, v in pool.items() if v["mean_brier"] is not None]
    if len(brier_ranked) >= 2:
        cb, vb = min(brier_ranked, key=lambda kv: kv[1]["mean_brier"])
        cw, vw = max(brier_ranked, key=lambda kv: kv[1]["mean_brier"])
        insights.append(
            f"Most predictable: **{cb}** (Brier {vb['mean_brier']}); "
            f"hardest: **{cw}** (Brier {vw['mean_brier']}) — size conviction accordingly.")

    roied = [(c, v) for c, v in cats.items()
             if v.get("roi") is not None and v["n_positions"] >= 3]
    if roied:
        c, v = max(roied, key=lambda kv: kv[1]["roi"])
        insights.append(
            f"Best simulated position ROI in **{c}**: {v['roi']:+.3f}/$1 over "
            f"{v['n_positions']} bets (frictionless — treat as a ceiling).")

    small = sorted(c for c, v in cats.items() if v["n"] < min_n)
    if small:
        insights.append(f"Thin samples (n<{min_n}, treat as noise): {', '.join(small)}.")
    return insights


def _candidate_lessons(report: dict) -> list[str]:
    """Heuristic lessons from systematic mistakes — proposals, not auto-saved."""
    lessons: list[str] = []
    strat = report["strategy"]

    cats = report.get("categories")
    if cats:
        big = {c: v for c, v in cats.items()
               if v["n"] >= 6 and v["edge_vs_market"] is not None}
        if big:
            c, v = max(big.items(), key=lambda kv: kv[1]["edge_vs_market"])
            if v["edge_vs_market"] > 0.01:
                lessons.append(
                    f"category '{c}' showed the best edge ({v['edge_vs_market']:+.3f} "
                    f"Brier vs crowd, n={v['n']}) → prioritise {c} markets and confirm "
                    f"the edge holds out-of-sample before raising conviction.")

    if report.get("edge_vs_market") is not None and report["edge_vs_market"] <= 0:
        lessons.append(
            f"strategy '{strat}' did NOT beat the crowd over this window "
            f"(edge_vs_market {report['edge_vs_market']:+.3f}) → do not deploy it "
            f"mechanically; ApexMind's edge must come from reasoning, not this rule.")

    roi = report.get("positions", {}).get("roi")
    npos = report.get("positions", {}).get("n", 0)
    if roi is not None and npos >= 5 and roi < 0:
        lessons.append(
            f"POSITIONs from '{strat}' lost money ({roi:+.3f}/$1 over {npos} bets) → "
            f"raise MIN_EDGE or require a real mechanism before taking these.")

    for b in report.get("calibration", {}).get("buckets", []):
        if b["n"] >= 5 and abs(b["gap"]) >= 0.12:
            direction = "over" if b["gap"] > 0 else "under"
            lessons.append(
                f"in the {b['band']} band we {direction}-forecast by {b['gap']:+.2f} "
                f"(n={b['n']}) → shade estimates {'down' if b['gap'] > 0 else 'up'} there.")

    hr = report.get("positions", {}).get("hit_rate")
    if hr is not None and npos >= 8 and hr < 0.5:
        lessons.append(
            f"POSITION hit-rate {hr:.0%} (<50%) over {npos} bets → the gate let through "
            f"too many weak edges; tighten MIN_EDGE/MIN_CONFIDENCE.")

    if not lessons:
        lessons.append(
            f"'{strat}' showed a small, consistent edge this window — promising, but "
            f"confirm it holds out-of-sample before raising conviction.")
    return lessons[:6]


def run_backtest(days: int = 90, strategy: str | None = None,
                 min_edge: float | None = None, min_confidence: float | None = None,
                 lead_days: int | None = None, window_days: int | None = None,
                 market_ids: list[str] | None = None, limit: int | None = None,
                 by_category: bool = False,
                 progress: Callable[[str], None] | None = None) -> dict:
    """Run the full backtest and return a structured report dict."""
    strategy = (strategy or config.BACKTEST_STRATEGY).lower()
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy '{strategy}'; choose from {list(STRATEGIES)}")
    min_edge = config.MIN_EDGE if min_edge is None else min_edge
    min_confidence = config.MIN_CONFIDENCE if min_confidence is None else min_confidence
    lead_days = config.BACKTEST_LEAD_DAYS if lead_days is None else lead_days
    window_days = config.BACKTEST_WINDOW_DAYS if window_days is None else window_days

    def say(msg: str):
        if progress:
            progress(msg)

    target = config.BACKTEST_MAX_MARKETS if limit is None else limit
    if market_ids:
        markets = replay_historical_markets(market_ids=market_ids)
        target = len(markets)
    else:
        # Fetch a larger pool than the target — many recent closes are short/untraded
        # and get skipped, so `limit` means "scored markets", not "markets examined".
        pool_cap = min(max(target * 6, 150), 400)
        markets = replay_historical_markets(days=days, limit=pool_cap)
    say(f"examining up to {len(markets)} traded resolved markets "
        f"(target {target} scored)…")

    sims: list[dict] = []
    skipped_history = 0
    examined = 0
    for m in markets:
        examined += 1
        sim = simulate_market(m, strategy, lead_days, window_days, min_edge, min_confidence)
        if sim is None:
            skipped_history += 1
        else:
            sims.append(sim)
        if progress and examined % 20 == 0:
            say(f"  …{examined} examined, {len(sims)} scored")
        if len(sims) >= target:
            break
        time.sleep(config.BACKTEST_FETCH_DELAY)

    leads = sorted(s["lead_hours"] for s in sims)
    median_lead = leads[len(leads) // 2] if leads else None

    model_report = scoring.calibration_report(sims)
    # Market baseline: same markets, but model_prob == the crowd snapshot.
    market_report = scoring.calibration_report(
        [{**s, "model_prob": s["market_prob"]} for s in sims])

    positions = [s for s in sims if s["decision"] == "POSITION"]
    pnls = [_pnl(p) for p in positions]
    roi = round(sum(pnls) / len(pnls), 4) if pnls else None
    pos_yes = [p for p in positions if p["direction"] == "YES"]
    pos_no = [p for p in positions if p["direction"] == "NO"]

    def _won(p):
        # The bet side (relative to the market) resolved in our favour.
        return (p["direction"] == "YES") == (p["outcome"] == 1)

    def _dir_hit(pos):
        return round(sum(_won(p) for p in pos) / len(pos), 3) if pos else None

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy,
        "params": {"days": days, "lead_days": lead_days, "window_days": window_days,
                   "min_edge": min_edge, "min_confidence": min_confidence},
        "n_markets_examined": examined,
        "n_scored": len(sims),
        "n_skipped_no_history": skipped_history,
        "median_lead_hours": median_lead,
        "mean_brier": model_report.get("mean_brier"),
        "market_baseline_brier": model_report.get("market_baseline_brier"),
        "edge_vs_market": model_report.get("edge_vs_market"),
        "model_calib_error": _calib_error(model_report.get("buckets", [])),
        "market_calib_error": _calib_error(market_report.get("buckets", [])),
        "calibration": model_report,
        "positions": {
            "n": len(positions),
            "hit_rate": _dir_hit(positions),
            "avg_edge": round(sum(abs(p["edge"]) for p in positions) / len(positions), 4)
                        if positions else None,
            "roi": roi,
            "yes_n": len(pos_yes), "yes_hit": _dir_hit(pos_yes),
            "no_n": len(pos_no), "no_hit": _dir_hit(pos_no),
        },
        "rolling_brier": _rolling_brier(sims),
        "worst_misses": sorted(sims, key=lambda s: s["brier"], reverse=True)[:5],
    }

    if by_category:
        grouped: dict[str, list[dict]] = {}
        for s in sims:
            grouped.setdefault(s["category"], []).append(s)
        report["categories"] = {
            c: _category_metrics(v)
            for c, v in sorted(grouped.items(), key=lambda kv: -len(kv[1]))}
        report["category_insights"] = _category_insights(report["categories"])

    report["candidate_lessons"] = _candidate_lessons(report)
    return report


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def format_report_md(r: dict) -> str:
    p = r["params"]
    L: list[str] = []
    L.append(f"# ApexMind Backtest — strategy `{r['strategy']}`")
    L.append(f"_generated {r['generated_at']} · lead {p['lead_days']}d · "
             f"gate edge≥{p['min_edge']} conf≥{p['min_confidence']}_\n")
    L.append(f"- resolved markets examined: **{r['n_markets_examined']}** "
             f"(scored {r['n_scored']}, skipped {r['n_skipped_no_history']} for thin/short history)")
    L.append(f"- median snapshot lead: **{r.get('median_lead_hours')}h** before resolution\n")

    L.append("## Accuracy")
    L.append(f"- model mean Brier: **{r['mean_brier']}**  ·  "
             f"market baseline: {r['market_baseline_brier']}  ·  "
             f"**edge vs market: {r['edge_vs_market']:+}** "
             f"({'model beat crowd' if (r['edge_vs_market'] or 0) > 0 else 'crowd won'})")
    L.append(f"- calibration error (n-weighted |gap|): model {r['model_calib_error']} "
             f"vs market {r['market_calib_error']}\n")

    pos = r["positions"]
    L.append("## Positions (passed the gate)")
    if pos["n"]:
        L.append(f"- count **{pos['n']}**  ·  hit-rate **{pos['hit_rate']}**  ·  "
                 f"avg |edge| {pos['avg_edge']}  ·  "
                 f"simulated ROI **{pos['roi']:+}/$1** _(frictionless, fills at snapshot)_")
        L.append(f"- by direction: YES {pos['yes_hit']} ({pos['yes_n']}) · "
                 f"NO {pos['no_hit']} ({pos['no_n']})\n")
    else:
        L.append("- none — no edge cleared the gate (a disciplined, common result)\n")

    L.append("## Calibration curve (model)")
    L.append("| band | n | avg_forecast | realised | gap |")
    L.append("|---|---|---|---|---|")
    for b in r["calibration"].get("buckets", []):
        L.append(f"| {b['band']} | {b['n']} | {b['avg_forecast']} | "
                 f"{b['realised_yes_rate']} | {b['gap']:+} |")
    L.append("\n_gap > 0 = over-forecast (predicted too high)._\n")

    if r.get("categories"):
        L.append("## By category")
        L.append("| category | n | model Brier | mkt Brier | edge | pos | hit | ROI/$1 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for c, v in r["categories"].items():
            edge = f"{v['edge_vs_market']:+}" if v["edge_vs_market"] is not None else "—"
            hit = v["hit_rate"] if v["hit_rate"] is not None else "—"
            roi = f"{v['roi']:+}" if v["roi"] is not None else "—"
            L.append(f"| {c} | {v['n']} | {v['mean_brier']} | "
                     f"{v['market_baseline_brier']} | {edge} | {v['n_positions']} | "
                     f"{hit} | {roi} |")
        L.append("\n_edge = market Brier − model Brier (positive = model beat the crowd)._")
        L.append("\n**Insights:**")
        for ins in r.get("category_insights", []):
            L.append(f"- {ins}")
        L.append("")

    L.append("## Rolling Brier (by resolution week)")
    for row in r["rolling_brier"]:
        bar = "█" * max(1, int(row["brier"] * 40))
        L.append(f"- `{row['period']}` n={row['n']:<3} brier={row['brier']:.3f} {bar}")
    L.append("")

    L.append("## Candidate lessons (review, then keep with `lesson \"…\"`)")
    for ln in r["candidate_lessons"]:
        L.append(f"- [backtest] {ln}")
    return "\n".join(L)


def summary_lines(r: dict) -> list[str]:
    """Compact console summary."""
    pos = r["positions"]
    out = [
        f"Backtest · strategy={r['strategy']} · scored {r['n_scored']} markets "
        f"(median lead {r.get('median_lead_hours')}h)",
        f"  model Brier {r['mean_brier']} vs market {r['market_baseline_brier']} "
        f"→ edge {r['edge_vs_market']:+}",
        f"  calibration error: model {r['model_calib_error']} vs market {r['market_calib_error']}",
    ]
    if pos["n"]:
        out.append(f"  positions {pos['n']} · hit-rate {pos['hit_rate']} · "
                   f"ROI {pos['roi']:+}/$1 · avg|edge| {pos['avg_edge']}")
    else:
        out.append("  positions 0 (no edge cleared the gate)")
    return out


def category_lines(r: dict) -> list[str]:
    """Compact per-category console block (empty if --by-category wasn't used)."""
    cats = r.get("categories")
    if not cats:
        return []
    out = ["", "By category:",
           f"  {'category':<10} {'n':>3} {'Brier':>6} {'mkt':>6} {'edge':>7} "
           f"{'pos':>3} {'hit':>5} {'ROI':>7}"]
    for c, v in cats.items():
        edge = f"{v['edge_vs_market']:+.3f}" if v["edge_vs_market"] is not None else "  —"
        hit = f"{v['hit_rate']:.2f}" if v["hit_rate"] is not None else "  —"
        roi = f"{v['roi']:+.3f}" if v["roi"] is not None else "  —"
        out.append(f"  {c:<10} {v['n']:>3} {v['mean_brier']:>6} "
                   f"{v['market_baseline_brier']:>6} {edge:>7} "
                   f"{v['n_positions']:>3} {hit:>5} {roi:>7}")
    out.append("Insights:")
    for ins in r.get("category_insights", []):
        out.append(f"  • {ins.replace('**', '')}")
    return out
