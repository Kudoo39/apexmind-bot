"""P2 — the research scraper degrades cleanly: well-formed empty results, never raises,
when the network is unavailable."""

from __future__ import annotations

import requests

import config
from tools import research


def test_gather_returns_wellformed_empty_offline(monkeypatch):
    monkeypatch.setattr(config, "RESEARCH_MIN_INTERVAL", 0)   # no throttle sleep

    def offline(*a, **k):
        raise requests.RequestException("no network")

    monkeypatch.setattr(research.requests, "get", offline)
    monkeypatch.setattr(research.requests, "post", offline)

    assert research.web_search("anything", use_cache=False) == []
    assert research.x_search("anything", use_cache=False) == []

    bundle = research.gather("a topic")
    assert bundle["web"] == [] and bundle["x"] == []
    assert bundle["query"] == "a topic"
    assert "fetched_at" in bundle


def test_browse_page_offline_is_not_ok(monkeypatch):
    monkeypatch.setattr(config, "RESEARCH_MIN_INTERVAL", 0)

    def offline(*a, **k):
        raise requests.RequestException("no network")

    monkeypatch.setattr(research.requests, "get", offline)
    page = research.browse_page("https://example.com", use_cache=False)
    assert page["ok"] is False and page["text"] == ""


def test_ddg_parser_handles_empty_and_garbage_html():
    assert research._parse_ddg_html("", 5) == []
    assert research._parse_ddg_html("<html><body>nothing here</body></html>", 5) == []


def test_empty_query_short_circuits():
    assert research.web_search("   ") == []
    assert research.x_search("") == []
