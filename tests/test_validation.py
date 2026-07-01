"""P0-1 — input validation at record/revise: a typo like model_prob=52 (meant 0.52)
must be rejected loudly, not flow silently into Brier/calibration/every report.
Unlike the dedup guard these are not judgment calls — a probability outside [0, 1]
can never be intended — so violations raise ValueError."""

from __future__ import annotations

import json

import pytest

import config
import main_agent
from tools import memory_store
from conftest import make_pred


# --------------------------------------------------------------------------- #
# record_prediction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field", ["model_prob", "confidence", "market_prob",
                                   "prior_prob"])
@pytest.mark.parametrize("bad", [52, 1.5, -0.1, "0.5", True])
def test_record_rejects_bad_probability_field(field, bad):
    with pytest.raises(ValueError):
        memory_store.record_prediction(make_pred(**{field: bad}))
    assert memory_store.load_predictions() == []          # nothing was written


def test_record_rejects_bad_decision():
    with pytest.raises(ValueError):
        memory_store.record_prediction(make_pred(decision="HOLD"))


def test_record_rejects_bad_direction():
    with pytest.raises(ValueError):
        memory_store.record_prediction(make_pred(direction="LONG"))


@pytest.mark.parametrize("bad", [0, 6, 2.5, "3", True])
def test_record_rejects_bad_conviction(bad):
    with pytest.raises(ValueError):
        memory_store.record_prediction(make_pred(conviction=bad))


def test_record_accepts_fully_valid_entry():
    p = memory_store.record_prediction(make_pred(model_prob=0.42, confidence=0.62,
                                                 decision="POSITION", direction="NO",
                                                 conviction=3))
    assert p["pred_id"] and p["status"] == "open"
    assert memory_store.load_predictions()[0]["model_prob"] == 0.42


def test_record_accepts_none_fields():
    # None means "not stated" — that stays allowed everywhere it is today.
    p = memory_store.record_prediction({
        "market_id": "m-none", "model_prob": None, "market_prob": None,
        "confidence": None, "decision": None, "direction": None, "conviction": None,
    })
    assert p["status"] == "open"


def test_record_accepts_boundary_probs():
    p = memory_store.record_prediction(make_pred(market_id="m-bound",
                                                 model_prob=0.0, market_prob=1.0))
    assert p["edge"] == -1.0


# --------------------------------------------------------------------------- #
# revise
# --------------------------------------------------------------------------- #
def test_revise_rejects_out_of_range_model_prob():
    p = memory_store.record_prediction(make_pred(market_id="m-rev"))
    with pytest.raises(ValueError):
        memory_store.revise(p["pred_id"], {"model_prob": 1.5})
    assert memory_store.load_predictions()[0]["model_prob"] == 0.5   # untouched


def test_revise_rejects_bad_decision_and_conviction():
    p = memory_store.record_prediction(make_pred(market_id="m-rev2"))
    with pytest.raises(ValueError):
        memory_store.revise(p["pred_id"], {"decision": "MAYBE"})
    with pytest.raises(ValueError):
        memory_store.revise(p["pred_id"], {"conviction": 9})


def test_revise_valid_patch_still_works():
    p = memory_store.record_prediction(make_pred(market_id="m-rev3"))
    upd = memory_store.revise(p["pred_id"], {"model_prob": 0.62, "confidence": 0.7})
    assert upd["model_prob"] == 0.62


# --------------------------------------------------------------------------- #
# CLI: cmd_record catches the ValueError and exits cleanly (no traceback)
# --------------------------------------------------------------------------- #
def test_cli_record_exits_cleanly_on_bad_input(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    f = tmp_path / "bad.json"
    f.write_text(json.dumps({"market_id": "m", "model_prob": 52}), encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        main_agent.main(["record", "--file", str(f)])
    assert "model_prob" in str(ei.value)                  # clear message, not a crash
    assert memory_store.load_predictions() == []
