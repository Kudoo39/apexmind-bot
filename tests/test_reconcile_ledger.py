"""reconcile_ledger — the log-odds identity  logit(model) ≈ logit(prior) + Σ ln(LR)."""

from __future__ import annotations

from tools import memory_store


def test_reconciles_when_lrs_match_model():
    # prior 0.5, two LRs of 2.0 -> implied prob ~0.8; recorded 0.8 -> reconciles.
    entry = {"prior_prob": 0.50, "model_prob": 0.80,
             "evidence": [{"likelihood_ratio": 2.0}, {"likelihood_ratio": 2.0}]}
    r = memory_store.reconcile_ledger(entry)
    assert r is not None
    assert r["ok"] is True
    assert abs(r["implied_model_prob"] - 0.8) < 0.02


def test_flags_unexplained_drift():
    # LR of 5x from 0.5 implies ~0.83, but the call recorded 0.5 -> does not reconcile.
    entry = {"prior_prob": 0.50, "model_prob": 0.50,
             "evidence": [{"likelihood_ratio": 5.0}]}
    r = memory_store.reconcile_ledger(entry)
    assert r is not None
    assert r["ok"] is False
    assert r["gap"] < -0.5            # recorded far below the implied logit


def test_none_when_insufficient_inputs():
    assert memory_store.reconcile_ledger(
        {"prior_prob": None, "model_prob": 0.5, "evidence": []}) is None
    assert memory_store.reconcile_ledger(
        {"prior_prob": 0.5, "model_prob": 0.5, "evidence": []}) is None   # no LRs
    assert memory_store.reconcile_ledger(
        {"prior_prob": 0.5, "model_prob": 1.0,
         "evidence": [{"likelihood_ratio": 2.0}]}) is None                 # degenerate


def test_ignores_nonpositive_and_bool_lrs():
    # likelihood_ratio of True/0/-1 are not usable LRs and must be skipped.
    entry = {"prior_prob": 0.5, "model_prob": 0.8,
             "evidence": [{"likelihood_ratio": True}, {"likelihood_ratio": 0},
                          {"likelihood_ratio": -1}, {"likelihood_ratio": 2.0},
                          {"likelihood_ratio": 2.0}]}
    r = memory_store.reconcile_ledger(entry)
    assert r is not None and r["ok"] is True      # only the two real 2.0x LRs counted
