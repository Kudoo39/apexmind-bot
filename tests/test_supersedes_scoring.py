"""P0.3 — a re-analysis tagged `supersedes` must be counted ONCE in calibration
(mirroring portfolio), not twice."""

from __future__ import annotations

import warnings

from tools import memory_store, portfolio
from conftest import make_pred


def _record_silently(**over):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")          # the dedup-guard UserWarning is expected
        return memory_store.record_prediction(make_pred(**over))


def test_superseded_excluded_from_calibration():
    p1 = _record_silently(market_id="dup", model_prob=0.70, market_prob=0.50,
                          decision="POSITION", direction="YES")
    # Re-record the SAME open market -> dedup guard tags it as superseding p1.
    p2 = _record_silently(market_id="dup", model_prob=0.72, market_prob=0.50,
                          decision="POSITION", direction="YES")
    assert p2.get("supersedes") == [p1["pred_id"]]

    memory_store.resolve_prediction(p1["pred_id"], 1)
    memory_store.resolve_prediction(p2["pred_id"], 1)

    rep = memory_store.recompute_calibration()
    assert rep["n"] == 1                          # the bet is scored once, not twice


def test_superseded_ids_helper_shared_definition():
    p1 = _record_silently(market_id="dup2", decision="POSITION", direction="YES")
    p2 = _record_silently(market_id="dup2", decision="POSITION", direction="YES")
    sup = memory_store.superseded_ids()
    assert sup == {p1["pred_id"]}
    # Portfolio uses the same definition: the superseded row drops out of exposure.
    rep = portfolio.exposure_report()
    assert rep["n_positions"] == 1
    assert p2["pred_id"] is not None


def test_resolved_predictions_dedupes():
    p1 = _record_silently(market_id="dup3", model_prob=0.6, market_prob=0.5,
                          decision="POSITION", direction="YES")
    p2 = _record_silently(market_id="dup3", model_prob=0.65, market_prob=0.5,
                          decision="POSITION", direction="YES")
    memory_store.resolve_prediction(p1["pred_id"], 1)
    memory_store.resolve_prediction(p2["pred_id"], 1)
    deduped = memory_store.resolved_predictions()
    raw = memory_store.resolved_predictions(include_superseded=True)
    assert len(deduped) == 1 and len(raw) == 2
