"""P0.5 — guarded in-place revise(): updates analysis fields on an OPEN row, refuses
protected fields and resolved rows."""

from __future__ import annotations

import pytest

from tools import memory_store
from conftest import make_pred


def test_revise_updates_normal_field_and_recomputes_edge():
    p = memory_store.record_prediction(make_pred(market_id="v", model_prob=0.40,
                                                 market_prob=0.50, confidence=0.50))
    upd = memory_store.revise(p["pred_id"], {"confidence": 0.70, "model_prob": 0.62})
    assert upd["confidence"] == 0.70
    assert upd["model_prob"] == 0.62
    assert upd["edge"] == round(0.62 - 0.50, 4)        # edge re-derived


def test_revise_normalises_patched_evidence():
    p = memory_store.record_prediction(make_pred(market_id="v-ev"))
    upd = memory_store.revise(p["pred_id"], {"evidence": [
        {"source_tier": "PRIMARY", "key_finding": "x", "likelihood_ratio": 2.0}]})
    assert upd["evidence"][0]["source_tier"] == "primary"   # normalised
    assert "fetched_at" in upd["evidence"][0]


@pytest.mark.parametrize("field", ["pred_id", "created_at", "status", "supersedes",
                                   "outcome", "brier", "resolved_at"])
def test_revise_refuses_protected_field(field):
    p = memory_store.record_prediction(make_pred(market_id=f"v-{field}"))
    with pytest.raises(ValueError):
        memory_store.revise(p["pred_id"], {field: "tampered"})


def test_revise_refuses_resolved_row():
    p = memory_store.record_prediction(make_pred(market_id="v-res",
                                                 decision="POSITION", direction="NO"))
    memory_store.resolve_prediction(p["pred_id"], 0)
    with pytest.raises(ValueError):
        memory_store.revise(p["pred_id"], {"confidence": 0.9})


def test_revise_missing_returns_none():
    assert memory_store.revise("p99999", {"confidence": 0.5}) is None


def test_revise_rejects_non_dict_patch():
    # A malformed CLI payload (e.g. a JSON array) must be rejected, not silently
    # mishandled. cmd_revise catches this TypeError and exits cleanly.
    p = memory_store.record_prediction(make_pred(market_id="v-nondict"))
    with pytest.raises(TypeError):
        memory_store.revise(p["pred_id"], [1, 2, 3])
