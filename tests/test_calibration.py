"""P1.6 — small-sample guards: Wilson 95% CI + `reliable` flag, with edge_vs_market
preserved exactly."""

from __future__ import annotations

import config
from tools import scoring


def _resolved(model_prob, outcome, market_prob=None, n=1):
    row = {"status": "resolved", "model_prob": model_prob, "outcome": outcome}
    if market_prob is not None:
        row["market_prob"] = market_prob
    return [dict(row) for _ in range(n)]


def _bucket(report):
    assert len(report["buckets"]) == 1
    return report["buckets"][0]


def test_wilson_ci_brackets_realised_rate_and_widens_as_n_shrinks():
    # Same realised rate (0.5), same band (40-50%), different n.
    small = _resolved(0.45, 1, n=2) + _resolved(0.45, 0, n=2)     # n=4
    big = _resolved(0.45, 1, n=20) + _resolved(0.45, 0, n=20)     # n=40

    bs = _bucket(scoring.calibration_report(small))
    bb = _bucket(scoring.calibration_report(big))

    # CI brackets the realised rate in both.
    assert bs["ci_low"] <= bs["realised_yes_rate"] <= bs["ci_high"]
    assert bb["ci_low"] <= bb["realised_yes_rate"] <= bb["ci_high"]

    # And it widens as n shrinks.
    assert (bs["ci_high"] - bs["ci_low"]) > (bb["ci_high"] - bb["ci_low"])


def test_reliable_flips_at_threshold():
    assert config.MIN_BUCKET_N == 8
    seven = _resolved(0.05, 0, n=7)
    eight = _resolved(0.05, 0, n=8)
    assert _bucket(scoring.calibration_report(seven))["reliable"] is False
    assert _bucket(scoring.calibration_report(eight))["reliable"] is True


def test_edge_vs_market_preserved():
    # Model nails 3 YES outcomes at 0.9; crowd only said 0.6 -> model beats crowd.
    res = _resolved(0.9, 1, market_prob=0.6, n=3)
    rep = scoring.calibration_report(res)
    assert rep["market_baseline_brier"] is not None
    assert rep["edge_vs_market"] is not None
    assert rep["edge_vs_market"] > 0                 # +ve == ApexMind beat the crowd
    # exact arithmetic: market Brier (0.16) - model Brier (0.01) = 0.15
    assert rep["edge_vs_market"] == round((0.6 - 1) ** 2 - (0.9 - 1) ** 2, 4)


def test_empty_report_shape():
    rep = scoring.calibration_report([])
    assert rep["n"] == 0 and rep["edge_vs_market"] is None and rep["buckets"] == []
