"""P0-3 — the shortlist's volume term `(1 + v ** 0.0001)` was dead weight:
v=1 → 2.0000, v=5,000,000 → 2.0015 — effectively a binary "has any volume" flag
with zero discrimination. The replacement must be genuinely monotone (0 < 100 <
1M) and dampened (log-scale, so a marquee market can't swamp the other terms)."""

from __future__ import annotations

from tools import polymarket


def test_volume_weight_discriminates():
    w0 = polymarket._volume_weight(0.0)
    w100 = polymarket._volume_weight(100.0)
    w1m = polymarket._volume_weight(1_000_000.0)
    assert w0 == 1.0                     # dead market → neutral multiplier, lowest
    assert w0 < w100 < w1m               # strictly monotone in 24h volume
    # Meaningfully larger — the old exponent gave w1m − w100 ≈ 0.001.
    assert w1m - w100 > 0.2


def test_volume_weight_is_dampened_not_linear():
    # 10× the volume must give nowhere near 10× the weight, so raw volume can't
    # swamp the category-priority / analysability terms.
    w = polymarket._volume_weight
    assert w(10_000_000.0) / w(1_000_000.0) < 1.2


def test_volume_weight_tolerates_junk():
    # Gamma sometimes serves 0/negative junk — the weight must stay sane.
    assert polymarket._volume_weight(-5.0) == 1.0


def _mk(vol24, mid):
    return {
        "id": mid, "question": f"Will the government of X do Y? ({mid})",
        "market_prob": 0.45, "liquidity": 50_000.0, "volume": 100_000.0,
        "volume_24hr": vol24, "days_to_resolution": 30.0,
    }


def test_shortlist_ranks_by_24h_volume_all_else_equal():
    lo, hi = _mk(100.0, "lo"), _mk(1_000_000.0, "hi")
    out = polymarket.shortlist([lo, hi], size=2)
    assert [m["id"] for m in out] == ["hi", "lo"]
