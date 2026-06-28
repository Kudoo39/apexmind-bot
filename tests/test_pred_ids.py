"""P0.2 — collision-proof prediction ids (max numeric id + 1, not len()+1)."""

from __future__ import annotations

from tools import memory_store
from conftest import make_pred


def test_ids_unique_after_handedit_delete():
    # Start with three rows p00001..p00003.
    for i in range(3):
        memory_store.record_prediction(make_pred(market_id=f"m{i}"))
    ids = [p["pred_id"] for p in memory_store.load_predictions()]
    assert ids == ["p00001", "p00002", "p00003"]

    # Hand-delete the middle row (the README invites hand-editing).
    preds = [p for p in memory_store.load_predictions() if p["pred_id"] != "p00002"]
    memory_store.save_predictions(preds)

    # The next record must NOT collide with the surviving p00003.
    new = memory_store.record_prediction(make_pred(market_id="m-new"))
    assert new["pred_id"] == "p00004"             # max(1,3)+1 — not len()+1 == 3

    all_ids = [p["pred_id"] for p in memory_store.load_predictions()]
    assert len(all_ids) == len(set(all_ids))      # every id distinct


def test_backtest_ids_ignored_for_numbering():
    # Non-`p` ids (e.g. backtest 'bt-...') must not perturb the counter.
    preds = [{"pred_id": "bt-123", "status": "resolved"},
             {"pred_id": "p00001", "status": "open"}]
    memory_store.save_predictions(preds)
    new = memory_store.record_prediction(make_pred(market_id="z"))
    assert new["pred_id"] == "p00002"
