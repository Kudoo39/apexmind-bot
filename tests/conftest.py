"""
Shared pytest fixtures for the ApexMind test suite.

Every test runs against a throwaway book in a tmp dir — the autouse `isolated_book`
fixture repoints all of `config`'s file paths there, so tests NEVER read or write the
real `memory/` / `data/`. config functions read `config.<PATH>` at call time (not
import time), so monkeypatching the module attributes is enough.

All tests must run offline: any module that does HTTP (tools/research.py,
tools/polymarket.py) is mocked at the call site by the individual test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the repo root importable (config.py, tools/, main_agent.py live there).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402  (after sys.path setup)


@pytest.fixture(autouse=True)
def isolated_book(tmp_path, monkeypatch):
    """Point every config file path at an isolated tmp dir for the duration of a test."""
    mem = tmp_path / "memory"
    data = tmp_path / "data"
    cache = data / "research_cache"
    for d in (mem, data, cache):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "MEMORY_DIR", mem)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "RESEARCH_CACHE_DIR", cache)
    monkeypatch.setattr(config, "PREDICTIONS_FILE", mem / "predictions.json")
    monkeypatch.setattr(config, "CALIBRATION_FILE", mem / "calibration.json")
    monkeypatch.setattr(config, "BELIEFS_FILE", mem / "beliefs.json")
    monkeypatch.setattr(config, "LESSONS_FILE", mem / "lessons.md")
    monkeypatch.setattr(config, "MARKETS_CACHE", data / "markets_latest.json")
    monkeypatch.setattr(config, "BRIEFING_FILE", data / "briefing_latest.md")
    monkeypatch.setattr(config, "REFLECTION_PACKET", data / "reflection_packet.json")
    monkeypatch.setattr(config, "BACKTEST_REPORT_MD", data / "backtest_report.md")
    monkeypatch.setattr(config, "BACKTEST_REPORT_JSON", data / "backtest_report.json")
    yield tmp_path


def make_pred(**over):
    """A minimal valid prediction-record kwargs dict, overridable per test."""
    base = {
        "market_id": "m-test",
        "model_prob": 0.5,
        "market_prob": 0.5,
        "confidence": 0.6,
        "decision": "PASS",
        "direction": "NO",
        "conviction": 1,
    }
    base.update(over)
    return base
