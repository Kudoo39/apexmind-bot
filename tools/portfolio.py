"""
tools/portfolio.py — exposure & correlation view over open positions.

The Supervisor (§1.5) reasons about correlation — "are five 'positions' really one
macro bet?" — but had no data to do it with. This gives it eyes: it groups open
POSITIONs by **category** and by a coarser **shared factor** (e.g. all of
Iran/Taiwan/Greenland load on `global-conflict`), sums conviction, nets direction,
and flags concentration so a single shock can't sink the book.

Pure read over file-based memory — no network.
"""

from __future__ import annotations

from typing import Any

import config
from tools import memory_store, polymarket

# Coarse correlated factors — markets sharing one tend to resolve together.
_FACTOR_KEYWORDS: list[tuple[str, list[str]]] = [
    ("global-conflict", ["invade", "invasion", " war ", "ceasefire", "regime",
                         "nuclear", "annex", "military", "strike", "missile",
                         "troops", "attack"]),
    ("us-rates", ["fed ", "rate cut", "rate hike", "interest rate", "fomc",
                  "inflation", " cpi", "recession"]),
    ("us-politics", ["trump", "biden", "harris", "election", "senate", "congress",
                     "president", "nominee", "impeach"]),
    ("crypto-beta", [" btc", "bitcoin", "ethereum", " eth ", "solana", " crypto",
                     "token"]),
]


def _factor(market: dict[str, Any]) -> str:
    q = " " + (market.get("question") or "").lower() + " "
    for fac, kws in _FACTOR_KEYWORDS:
        if any(k in q for k in kws):
            return fac
    return (market.get("category") or "other").lower()


def exposure_report() -> dict[str, Any]:
    """Aggregate open POSITIONs by category and shared factor, with risk flags."""
    positions = [p for p in memory_store.open_predictions()
                 if p.get("decision") == "POSITION"]

    by_cat: dict[str, dict[str, Any]] = {}
    by_factor: dict[str, dict[str, Any]] = {}
    total_conv = 0
    for p in positions:
        cat = p.get("category") or polymarket.categorize(p)
        fac = _factor(p)
        conv = int(p.get("conviction") or 1)
        signed = conv if p.get("direction") == "YES" else -conv
        total_conv += conv
        for key, store in ((cat, by_cat), (fac, by_factor)):
            d = store.setdefault(key, {"n": 0, "conviction": 0, "net": 0, "preds": []})
            d["n"] += 1
            d["conviction"] += conv
            d["net"] += signed
            d["preds"].append(p.get("pred_id"))

    flags = []
    cap_c = config.PORTFOLIO_MAX_FACTOR_CONVICTION
    cap_n = config.PORTFOLIO_MAX_FACTOR_POSITIONS
    for fac, d in sorted(by_factor.items(), key=lambda kv: -kv[1]["conviction"]):
        if d["conviction"] >= cap_c:
            flags.append(f"factor '{fac}': {d['conviction']} conviction over {d['n']} "
                         f"positions — exceeds the {cap_c} cap (correlated; cut size).")
        elif d["n"] >= cap_n:
            flags.append(f"factor '{fac}': {d['n']} positions share one driver — "
                         "treat as a single bet, not diversification.")

    return {"n_positions": len(positions), "total_conviction": total_conv,
            "by_category": by_cat, "by_factor": by_factor, "flags": flags}


def _fmt_groups(groups: dict[str, dict[str, Any]]) -> list[str]:
    rows = []
    for k, d in sorted(groups.items(), key=lambda kv: -kv[1]["conviction"]):
        lean = "YES" if d["net"] > 0 else "NO" if d["net"] < 0 else "flat"
        rows.append(f"  {k:<16} n={d['n']:<2} conviction={d['conviction']:<3} "
                    f"net={lean} ({d['net']:+})")
    return rows


def format_exposure_md(r: dict[str, Any]) -> str:
    if not r["n_positions"]:
        return "_no open positions — exposure is flat._"
    L = [f"**Open exposure** — {r['n_positions']} positions, "
         f"{r['total_conviction']} total conviction\n", "_By factor:_"]
    L += _fmt_groups(r["by_factor"])
    L.append("\n_By category:_")
    L += _fmt_groups(r["by_category"])
    if r["flags"]:
        L.append("\n⚠ **Concentration flags:**")
        L += [f"- {f}" for f in r["flags"]]
    return "\n".join(L)


def summary_lines(r: dict[str, Any]) -> list[str]:
    if not r["n_positions"]:
        return ["Portfolio: flat (no open positions)."]
    out = [f"Portfolio: {r['n_positions']} positions · {r['total_conviction']} conviction"]
    out += _fmt_groups(r["by_factor"])
    for f in r["flags"]:
        out.append(f"  ⚠ {f}")
    return out
