"""P0.1 — atomic writes: a crash between temp-write and os.replace must leave the
original book intact (never truncated/empty)."""

from __future__ import annotations

import os

import pytest

import config
from tools import atomic_io, memory_store
from conftest import make_pred


def test_replace_failure_leaves_original_intact(monkeypatch):
    # Write the book once (replace works).
    memory_store.record_prediction(make_pred(market_id="a", model_prob=0.5))
    original = config.PREDICTIONS_FILE.read_text(encoding="utf-8-sig")
    assert '"market_id": "a"' in original

    # Simulate a crash exactly at the rename step of the NEXT write.
    def boom(src, dst):
        raise OSError("simulated crash between temp-write and replace")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        memory_store.record_prediction(make_pred(market_id="b", model_prob=0.9))

    # NB: don't monkeypatch.undo() here — the autouse isolated_book fixture shares this
    # same monkeypatch instance, so undo() would also revert the tmp path patches and
    # we'd read the real book. The reads below don't need os.replace anyway.
    after = config.PREDICTIONS_FILE.read_text(encoding="utf-8-sig")
    assert after == original                      # intact — not truncated/empty
    assert '"market_id": "b"' not in after        # the failed write left no trace

    # And no temp file was left littering the directory.
    leftovers = list(config.PREDICTIONS_FILE.parent.glob(
        f".{config.PREDICTIONS_FILE.name}.*.tmp"))
    assert leftovers == []


def test_atomic_write_text_roundtrip(tmp_path):
    p = tmp_path / "nested" / "file.txt"
    atomic_io.atomic_write_text(p, "hello\nworld")
    assert p.read_text(encoding="utf-8") == "hello\nworld"


def test_atomic_write_json_roundtrip(tmp_path):
    import json
    p = tmp_path / "x.json"
    atomic_io.atomic_write_json(p, {"k": "vπ", "n": 3})
    assert json.loads(p.read_text(encoding="utf-8")) == {"k": "vπ", "n": 3}
