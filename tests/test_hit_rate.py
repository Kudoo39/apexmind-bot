"""P0-2 — hit_rate must score the actual bet side (`direction`, the sign of the edge
vs the MARKET price), not `model_prob >= 0.5`. Live-book case: model=0.52,
market=0.528, direction=NO — outcome 0 means the bet WON, but the old rule scored
it as a miss (and contradicted backtest._pnl, which settles by direction)."""

from __future__ import annotations

from tools import scoring


def _pos(**over):
    base = {"decision": "POSITION", "status": "resolved",
            "model_prob": 0.5, "market_prob": 0.5,
            "direction": "NO", "outcome": 0}
    base.update(over)
    return base


def test_direction_beats_model_prob_side():
    # The exact live-book failure: model 0.52 (>= 0.5) but the bet is NO because
    # the market sits at 0.528. Outcome 0 → the bet WON.
    p = _pos(model_prob=0.52, market_prob=0.528, direction="NO", outcome=0)
    assert scoring.hit_rate([p]) == {"n_positions": 1, "hit_rate": 1.0}


def test_direction_yes_losing_is_still_a_miss():
    p = _pos(model_prob=0.6, market_prob=0.5, direction="YES", outcome=0)
    assert scoring.hit_rate([p])["hit_rate"] == 0.0


def test_fallback_sign_of_edge_when_direction_missing():
    # No direction recorded: the bet side is the sign of (model_prob − market_prob).
    p = _pos(model_prob=0.52, market_prob=0.528, direction=None, outcome=0)
    assert scoring.hit_rate([p])["hit_rate"] == 1.0


def test_fallback_model_prob_when_no_market_prob_either():
    p = _pos(model_prob=0.52, market_prob=None, direction=None, outcome=1)
    assert scoring.hit_rate([p])["hit_rate"] == 1.0
    p2 = _pos(model_prob=0.4, market_prob=None, direction=None, outcome=0)
    assert scoring.hit_rate([p2])["hit_rate"] == 1.0


def test_consistent_with_backtest_direction_settlement():
    # Backtest pseudo-predictions always carry direction = sign(model − market);
    # hit_rate must agree with the backtest's own settlement rule
    # (`(direction == "YES") == (outcome == 1)` in backtest._won/_category_metrics).
    sims = [
        _pos(model_prob=0.7, market_prob=0.5, direction="YES", outcome=1),
        _pos(model_prob=0.7, market_prob=0.5, direction="YES", outcome=0),
        _pos(model_prob=0.3, market_prob=0.45, direction="NO", outcome=0),
        _pos(model_prob=0.52, market_prob=0.528, direction="NO", outcome=0),
    ]
    won = sum((s["direction"] == "YES") == (s["outcome"] == 1) for s in sims)
    rep = scoring.hit_rate(sims)
    assert rep["n_positions"] == len(sims)
    assert rep["hit_rate"] == round(won / len(sims), 3)
