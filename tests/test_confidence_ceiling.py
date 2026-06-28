"""suggest_confidence_ceiling — soft over-confidence advisory thresholds."""

from __future__ import annotations

from tools import memory_store


def _ev(lr, tier):
    return {"likelihood_ratio": lr, "source_tier": tier}


def test_deep_market_all_primary_movers_uncapped():
    c = memory_store.suggest_confidence_ceiling(100_000, [_ev(2.0, "primary")])
    assert c == 0.90


def test_thin_liquidity_caps_hard():
    # liq < 5_000 -> 0.65 even with solid primary evidence.
    c = memory_store.suggest_confidence_ceiling(1_000, [_ev(2.0, "primary")])
    assert c == 0.65


def test_mid_liquidity_cap():
    # 5_000 <= liq < 20_000 -> 0.75.
    c = memory_store.suggest_confidence_ceiling(10_000, [_ev(2.0, "primary")])
    assert c == 0.75


def test_no_movers_means_nothing_concrete():
    # Only context-only evidence (LR == 1) -> no real mover -> 0.60.
    c = memory_store.suggest_confidence_ceiling(100_000, [_ev(1.0, "primary")])
    assert c == 0.60


def test_majority_low_tier_movers():
    c = memory_store.suggest_confidence_ceiling(
        100_000, [_ev(2.0, "social"), _ev(0.5, "market")])
    assert c == 0.65


def test_minority_low_tier_movers():
    c = memory_store.suggest_confidence_ceiling(
        100_000, [_ev(2.0, "primary"), _ev(1.5, "expert"), _ev(0.5, "social")])
    assert c == 0.78
