"""
Scoring & calibration math for ApexMind.

Pure functions, no I/O. The Reflection role uses these numbers to judge process
vs. luck and to detect systematic bias.
"""

from __future__ import annotations

from typing import Any


def brier_score(prob_yes: float, outcome: int) -> float:
    """Brier score for a binary forecast. Lower is better (0 perfect, 1 worst).

    prob_yes : forecasted probability of YES
    outcome  : realised outcome, 1 (YES) or 0 (NO)
    """
    return (prob_yes - outcome) ** 2


def log_loss(prob_yes: float, outcome: int, eps: float = 1e-9) -> float:
    """Logarithmic (cross-entropy) loss. Punishes confident wrongness hard."""
    import math
    p = min(max(prob_yes, eps), 1 - eps)
    return -(outcome * math.log(p) + (1 - outcome) * math.log(1 - p))


def calibration_report(resolved: list[dict[str, Any]],
                       n_buckets: int = 10) -> dict[str, Any]:
    """Bucket resolved predictions and measure calibration.

    Returns mean Brier, a per-bucket table (forecast band vs realised YES rate),
    a naive 'market baseline' Brier for comparison, and a simple bias summary.
    """
    if not resolved:
        return {"n": 0, "mean_brier": None, "buckets": [],
                "market_baseline_brier": None, "edge_vs_market": None}

    buckets = [{"lo": i / n_buckets, "hi": (i + 1) / n_buckets,
                "n": 0, "sum_pred": 0.0, "sum_outcome": 0} for i in range(n_buckets)]

    total_brier = 0.0
    total_market_brier = 0.0
    have_market = 0
    for p in resolved:
        prob = p["model_prob"]
        outcome = int(p["outcome"])
        total_brier += brier_score(prob, outcome)

        if p.get("market_prob") is not None:
            total_market_brier += brier_score(p["market_prob"], outcome)
            have_market += 1

        idx = min(int(prob * n_buckets), n_buckets - 1)
        b = buckets[idx]
        b["n"] += 1
        b["sum_pred"] += prob
        b["sum_outcome"] += outcome

    n = len(resolved)
    table = []
    for b in buckets:
        if b["n"] == 0:
            continue
        table.append({
            "band": f"{b['lo']:.0%}-{b['hi']:.0%}",
            "n": b["n"],
            "avg_forecast": round(b["sum_pred"] / b["n"], 3),
            "realised_yes_rate": round(b["sum_outcome"] / b["n"], 3),
            "gap": round(b["sum_pred"] / b["n"] - b["sum_outcome"] / b["n"], 3),
        })

    mean_brier = round(total_brier / n, 4)
    market_brier = round(total_market_brier / have_market, 4) if have_market else None
    edge = round(market_brier - mean_brier, 4) if market_brier is not None else None

    return {
        "n": n,
        "mean_brier": mean_brier,
        "market_baseline_brier": market_brier,   # how the crowd did on the same set
        "edge_vs_market": edge,                  # +ve => ApexMind beat the market
        "buckets": table,
    }


def hit_rate(resolved: list[dict[str, Any]]) -> dict[str, Any]:
    """Directional accuracy among POSITION calls (did we pick the right side?)."""
    positions = [p for p in resolved
                 if p.get("decision") == "POSITION" and p.get("model_prob") is not None]
    if not positions:
        return {"n_positions": 0, "hit_rate": None}
    hits = 0
    for p in positions:
        predicted_yes = p["model_prob"] >= 0.5
        actual_yes = int(p["outcome"]) == 1
        if predicted_yes == actual_yes:
            hits += 1
    return {"n_positions": len(positions),
            "hit_rate": round(hits / len(positions), 3)}


# --------------------------------------------------------------------------- #
# Reflection analytics — leverage the P3 evidence ledger
# --------------------------------------------------------------------------- #
_LOWQ_TIERS = ("social", "market", "unspecified")


def _dominant_tier(evidence: list[dict[str, Any]]) -> str | None:
    """The source_tier of the entry that moved the estimate most (max |ln LR|)."""
    import math
    best_tier, best_mag = None, -1.0
    for e in evidence or []:
        lr = e.get("likelihood_ratio")
        if lr in (None, 0):
            continue
        try:
            mag = abs(math.log(float(lr)))
        except (ValueError, TypeError):
            continue
        if mag > best_mag:
            best_mag, best_tier = mag, e.get("source_tier", "unspecified")
    return best_tier  # None => ledger had only context (LR≈1) or no LRs


def evidence_tier_report(resolved: list[dict[str, Any]]) -> dict[str, Any]:
    """Brier grouped by each call's DOMINANT evidence tier.

    Answers the core P3 question: do primary/expert-anchored calls actually resolve
    better than social/market-anchored ones? `low_tier_share` tracks how much of our
    ledgered conviction rests on weak sources.
    """
    buckets: dict[str, dict[str, float]] = {}
    n_ledger = 0
    for p in resolved:
        if p.get("model_prob") is None or p.get("outcome") is None:
            continue
        ev = p.get("evidence") or []
        if ev:
            n_ledger += 1
            tier = _dominant_tier(ev) or "context_only"
        else:
            tier = "no_ledger"
        b = (p["model_prob"] - int(p["outcome"])) ** 2
        d = buckets.setdefault(tier, {"n": 0, "sum_brier": 0.0})
        d["n"] += 1
        d["sum_brier"] += b
    table = {t: {"n": int(d["n"]), "mean_brier": round(d["sum_brier"] / d["n"], 4)}
             for t, d in buckets.items()}
    lowq = sum(int(d["n"]) for t, d in buckets.items() if t in _LOWQ_TIERS)
    return {"n_with_ledger": n_ledger, "by_dominant_tier": table,
            "low_tier_share": round(lowq / n_ledger, 3) if n_ledger else None}


def category_report(resolved: list[dict[str, Any]]) -> dict[str, Any]:
    """Live per-category Brier / edge-vs-market / hit-rate (the backtest, but on
    ApexMind's own resolved track record)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for p in resolved:
        if p.get("model_prob") is None or p.get("outcome") is None:
            continue
        groups.setdefault(p.get("category") or "Uncategorized", []).append(p)
    out = {}
    for cat, ps in groups.items():
        rep = calibration_report(ps)
        hr = hit_rate(ps)
        out[cat] = {"n": rep["n"], "mean_brier": rep["mean_brier"],
                    "edge_vs_market": rep["edge_vs_market"],
                    "position_hit_rate": hr["hit_rate"],
                    "n_positions": hr["n_positions"]}
    return out
