"""P1-6 — auto-resolve must respect an explicit unresolved UMA status. A closed
market with a pinned price (≥0.99 / ≤0.01) can still be a pending/disputed UMA
settlement; settling the book on it records an outcome the oracle may reverse.
When umaResolutionStatus is present and NOT "resolved" → ambiguous-closed
(outcome=None). When the field is absent or already "resolved" → exactly today's
price-based behaviour."""

from __future__ import annotations

import pytest

from tools import polymarket


def _payload(**over):
    base = {
        "id": "55", "question": "Will X happen?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.995", "0.005"]',
        "closed": True,
    }
    base.update(over)
    return base


def _check(monkeypatch, payload):
    monkeypatch.setattr(polymarket, "get_market_by_id", lambda mid: payload)
    return polymarket.check_resolution("55")


def test_absent_uma_field_keeps_price_inference(monkeypatch):
    s = _check(monkeypatch, _payload())
    assert s["resolved"] is True and s["outcome"] == 1


def test_uma_resolved_keeps_price_inference(monkeypatch):
    s = _check(monkeypatch, _payload(umaResolutionStatus="resolved"))
    assert s["resolved"] is True and s["outcome"] == 1


@pytest.mark.parametrize("status", ["proposed", "disputed", "DISPUTED", "challenged"])
def test_pending_uma_makes_a_pinned_close_ambiguous(monkeypatch, status):
    # Closed AND price pinned at 0.995 — but UMA says the settlement isn't final.
    s = _check(monkeypatch, _payload(umaResolutionStatus=status))
    assert s["closed"] is True
    assert s["resolved"] is False            # auto-resolve must NOT settle this
    assert s["outcome"] is None              # → listed for manual resolve instead


def test_pending_uma_blocks_the_no_side_too(monkeypatch):
    s = _check(monkeypatch, _payload(umaResolutionStatus="proposed",
                                     outcomePrices='["0.004", "0.996"]'))
    assert s["resolved"] is False and s["outcome"] is None


def test_empty_uma_string_is_treated_as_absent(monkeypatch):
    s = _check(monkeypatch, _payload(umaResolutionStatus=""))
    assert s["resolved"] is True and s["outcome"] == 1
