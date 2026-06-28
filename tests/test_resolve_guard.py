"""P0.4 — refuse to re-resolve an already-resolved row (append-only book)."""

from __future__ import annotations

import pytest

from tools import memory_store
from conftest import make_pred


def _row(pred_id):
    return next(p for p in memory_store.load_predictions() if p["pred_id"] == pred_id)


def test_cannot_re_resolve():
    p = memory_store.record_prediction(make_pred(market_id="r", model_prob=0.6,
                                                 decision="POSITION", direction="YES"))
    memory_store.resolve_prediction(p["pred_id"], 1)
    first = _row(p["pred_id"])
    assert first["outcome"] == 1
    brier1 = first["brier"]

    # A second resolve (different outcome) must raise AND not mutate the settled row.
    with pytest.raises(ValueError):
        memory_store.resolve_prediction(p["pred_id"], 0)

    after = _row(p["pred_id"])
    assert after["outcome"] == 1 and after["brier"] == brier1


def test_resolve_missing_returns_none():
    assert memory_store.resolve_prediction("p99999", 1) is None
