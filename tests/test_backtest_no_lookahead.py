"""Backtest no-look-ahead: the snapshot price must come strictly from points at or
before the decision timestamp; truncating future bars must leave it identical."""

from __future__ import annotations

from tools import backtest, polymarket


def test_nearest_at_or_before_never_peeks():
    hist = [{"t": i * 100, "p": 0.5 + i * 0.01} for i in range(10)]   # t=0..900
    ts = 550
    chosen = backtest._nearest_at_or_before(hist, ts)
    assert chosen["t"] == 500                       # latest point with t <= ts
    assert chosen["t"] <= ts                        # never a future bar

    # Truncating every future bar (t > ts) leaves the choice unchanged.
    truncated = [h for h in hist if h["t"] <= ts]
    assert backtest._nearest_at_or_before(truncated, ts) == chosen

    # Adding future bars does not change it either (no look-ahead).
    extra = hist + [{"t": 10_000, "p": 0.99}]
    assert backtest._nearest_at_or_before(extra, ts) == chosen


def test_simulate_market_snapshot_is_pre_resolution(monkeypatch):
    # Synthetic whole-life history: ~100 days, hourly, price drifting inside [0,1].
    base = 1_000_000
    hist = [{"t": base + i * 3600, "p": 0.3 + 0.0001 * i} for i in range(24 * 100)]
    monkeypatch.setattr(polymarket, "price_history_full", lambda *a, **k: hist)

    m = {"id": "m1", "yes_token_id": "tok", "question": "Will it happen?",
         "outcome": 1, "liquidity": 50_000, "volume": 100_000,
         "resolved_at": "2026-01-01T00:00:00+00:00", "end_date": "2026-01-01"}

    sim = backtest.simulate_market(m, "market", lead_days=7, window_days=7,
                                   min_edge=0.08, min_conf=0.55)
    assert sim is not None

    last_t = hist[-1]["t"]
    # The snapshot is strictly before resolution (>= 30 min of lead).
    assert sim["snapshot_ts"] <= last_t - backtest._MIN_LEAD_SECS

    # The snapshot price equals a REAL bar at or before snapshot_ts...
    bar = backtest._nearest_at_or_before(hist, sim["snapshot_ts"])
    assert sim["market_prob"] == round(backtest._clamp(bar["p"]), 4)

    # ...and truncating all future bars yields the identical snapshot price.
    truncated = [h for h in hist if h["t"] <= sim["snapshot_ts"]]
    bar2 = backtest._nearest_at_or_before(truncated, sim["snapshot_ts"])
    assert backtest._clamp(bar2["p"]) == backtest._clamp(bar["p"])
