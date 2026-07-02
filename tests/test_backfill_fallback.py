"""P1-4 — cache-miss backfill: recording a market absent from data/markets_latest.json
used to leave question/category/liquidity blank, which mis-buckets the portfolio
factor (the docstring's own warning). On a cache miss the store now falls back to a
live Gamma lookup — best-effort only: any failure degrades silently to the old
blank-fields behaviour. All tests run offline via a monkeypatched get_market_by_id."""

from __future__ import annotations

import json

import pytest

import config
from tools import memory_store, polymarket
from conftest import make_pred

# A raw Gamma payload as _normalise() expects it (lists JSON-encoded as strings).
RAW_GAMMA = {
    "id": "999", "slug": "will-the-fed-hike",
    "question": "Will the Fed hike rates in 2026?",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.34", "0.66"]',
    "clobTokenIds": '["111", "222"]',
    "endDate": "2026-12-31T00:00:00Z",
    "volumeNum": 250000, "volume24hr": 1200, "liquidityNum": 90000,
}


def test_cache_miss_falls_back_to_gamma(monkeypatch):
    calls = []

    def fake_get(mid):
        calls.append(mid)
        return dict(RAW_GAMMA)

    monkeypatch.setattr(polymarket, "get_market_by_id", fake_get)
    p = memory_store.record_prediction(make_pred(market_id="999", market_prob=None))
    assert calls == ["999"]
    assert p["question"] == "Will the Fed hike rates in 2026?"
    assert p["market_prob"] == 0.34
    assert p["end_date"] == "2026-12-31T00:00:00Z"
    assert p["url"].endswith("will-the-fed-hike")
    assert p["liquidity"] == 90000.0
    assert p["category"] == "Economy"                 # via categorize(), not raw
    assert p["edge"] == round(0.5 - 0.34, 4)          # edge derived from the backfill


def test_network_failure_degrades_silently(monkeypatch):
    def boom(mid):
        raise RuntimeError("network down")

    monkeypatch.setattr(polymarket, "get_market_by_id", boom)
    p = memory_store.record_prediction(make_pred(market_id="999", market_prob=None))
    assert p["status"] == "open"                      # record still succeeds
    assert p.get("question") is None                  # fields simply stay blank


def test_gamma_returning_none_degrades_silently(monkeypatch):
    monkeypatch.setattr(polymarket, "get_market_by_id", lambda mid: None)
    p = memory_store.record_prediction(make_pred(market_id="999", market_prob=None))
    assert p["status"] == "open"
    assert p.get("question") is None


def test_cache_hit_never_touches_the_network(monkeypatch):
    cache = {"shortlist": [{"id": "42", "question": "Cached question?",
                            "market_prob": 0.6, "end_date": "2026-01-01",
                            "url": "https://example/42", "liquidity": 1000.0,
                            "category": "Politics"}]}
    config.MARKETS_CACHE.write_text(json.dumps(cache), encoding="utf-8")

    def boom(mid):
        raise AssertionError("network touched despite a cache hit")

    monkeypatch.setattr(polymarket, "get_market_by_id", boom)
    p = memory_store.record_prediction(make_pred(market_id="42", market_prob=None))
    assert p["question"] == "Cached question?"
    assert p["market_prob"] == 0.6


def test_complete_entry_skips_the_network(monkeypatch):
    def boom(mid):
        raise AssertionError("network touched for an already-complete entry")

    monkeypatch.setattr(polymarket, "get_market_by_id", boom)
    p = memory_store.record_prediction(make_pred(
        market_id="777", question="Q?", end_date="2026-01-01",
        url="https://example/777", liquidity=5000.0, category="Politics"))
    assert p["question"] == "Q?"
