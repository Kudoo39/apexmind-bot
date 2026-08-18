from __future__ import annotations

from tools import memory_store, notification, portfolio, trade_store
from conftest import make_pred


def _trade(**over):
    base = {"market_id": "m1", "pred_id": "p00001", "question": "Market?",
            "side": "NO", "status": "HOLDING", "entry_price": 0.50,
            "shares": 2.0, "current_price": 0.60}
    base.update(over)
    return base


def test_closed_trade_stays_separate_from_open_prediction():
    p = memory_store.record_prediction(make_pred(market_id="m1", model_prob=0.2,
                                                 decision="POSITION", direction="NO"))
    trade_store.sync_trades([_trade(pred_id=p["pred_id"], status="CLOSED",
                                    close_price=0.60)], replace=True)
    assert memory_store.open_predictions()[0]["status"] == "open"
    assert trade_store.analyse_trade(p)["trade_action"] == "CLOSED_NO_ACTION"


def test_hold_when_residual_edge_still_clears_gate():
    p = memory_store.record_prediction(make_pred(market_id="m1", model_prob=0.23,
                                                 confidence=0.7, decision="POSITION",
                                                 direction="NO"))
    trade_store.sync_trades([_trade(pred_id=p["pred_id"], entry_price=0.53,
                                    current_price=0.64)], replace=True)
    result = trade_store.analyse_trade(p)
    assert result["residual_edge"] == 0.13
    assert result["trade_action"] == "HOLD"
    assert result["take_profit_price"] == 0.69


def test_take_profit_after_target_when_edge_is_gone():
    p = memory_store.record_prediction(make_pred(market_id="m1", model_prob=0.13,
                                                 confidence=0.6, decision="POSITION",
                                                 direction="NO"))
    trade_store.sync_trades([_trade(pred_id=p["pred_id"], entry_price=0.76,
                                    current_price=0.895)], replace=True)
    result = trade_store.analyse_trade(p)
    assert result["residual_edge"] == -0.025
    assert result["trade_action"] == "TAKE_PROFIT"


def test_portfolio_prefers_actual_holdings_and_excludes_closed():
    held = memory_store.record_prediction(make_pred(market_id="held", question="Iran deal",
                                                    decision="PASS", direction="NO"))
    closed = memory_store.record_prediction(make_pred(market_id="closed", question="Fed hike",
                                                      decision="POSITION", direction="YES"))
    trade_store.sync_trades([
        _trade(market_id="held", pred_id=held["pred_id"], status="HOLDING"),
        _trade(market_id="closed", pred_id=closed["pred_id"], side="YES",
               status="CLOSED", close_price=0.60),
    ], replace=True)
    report = portfolio.exposure_report()
    assert report["source"] == "actual trades"
    assert report["n_positions"] == 1
    assert report["actual_trade_ids"] == ["t00001"]


def test_notification_names_trade_action_not_every_call_a_position():
    p = memory_store.record_prediction(make_pred(market_id="m1", model_prob=0.23,
                                                 confidence=0.7, decision="POSITION",
                                                 direction="NO", question="Out?"))
    trade_store.sync_trades([_trade(pred_id=p["pred_id"], entry_price=0.53,
                                    current_price=0.64)], replace=True)
    msg = notification.build_decision_message(
        positions=[trade_store.decorate_prediction(p)], ts="2026-07-12")
    assert "YOUR NEXT MOVE" in msg
    assert "ACTION QUEUE" in msg
    assert "No trade was placed automatically" in msg
    assert "[HOLD]" in msg
    assert "Position: NO" in msg
    assert "residual" in msg


def test_notification_labels_closed_trade_as_closed_not_held():
    p = memory_store.record_prediction(make_pred(market_id="m1", model_prob=0.3,
                                                 decision="POSITION", direction="YES",
                                                 question="Closed?"))
    trade_store.sync_trades([_trade(pred_id=p["pred_id"], side="YES", status="CLOSED",
                                    close_price=0.62)], replace=True)
    msg = notification.build_decision_message(
        positions=[trade_store.decorate_prediction(p)], ts="2026-07-12")
    assert "[CLOSED_NO_ACTION]" in msg
    assert "Closed: YES" in msg
    assert "Position: YES" not in msg
    assert "Re-entry requires a fresh explicit decision" in msg


def test_notification_puts_review_actions_before_holds():
    hold = {"question": "Hold this", "decision": "POSITION", "direction": "NO",
            "trade_action": "HOLD", "model_prob": 0.2, "market_prob": 0.4,
            "confidence": 0.7, "residual_edge": 0.2}
    review = {"question": "Review this", "decision": "PASS", "direction": "NO",
              "trade_action": "TAKE_PROFIT", "model_prob": 0.35,
              "market_prob": 0.4, "confidence": 0.6, "residual_edge": 0.05}
    msg = notification.build_decision_message(
        positions=[hold, review], bet_recommendation="Act on the queue.", ts="2026-07-12")
    assert msg.index("Review this") < msg.index("Hold this")
    assert "1 review now · 1 hold" in msg
