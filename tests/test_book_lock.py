"""P1-5 — cross-process advisory lock on the book. apex_daily.py (Task Scheduler)
runs auto-resolve while an operator may be recording in a Claude Code session; the
atomic writes keep each file write all-or-nothing, but two concurrent
load→mutate→save cycles still silently drop one writer's changes. Every mutating
path now holds memory/.predictions.lock (O_CREAT|O_EXCL, dependency-free) with a
~10s timeout and stale-lock breaking. Works on Windows (primary host) and POSIX."""

from __future__ import annotations

import os
import threading
import time

import pytest

import config
from tools import memory_store
from conftest import make_pred


def _lock_path():
    return config.PREDICTIONS_FILE.with_name(".predictions.lock")


def test_lock_released_after_normal_write():
    memory_store.record_prediction(make_pred())
    assert not _lock_path().exists()


def test_held_lock_times_out_with_clear_error(monkeypatch):
    monkeypatch.setattr(memory_store, "_LOCK_TIMEOUT", 0.3)
    lock = _lock_path()
    lock.write_text("pid=999999", encoding="utf-8")       # fresh foreign lock
    with pytest.raises(TimeoutError) as ei:
        memory_store.record_prediction(make_pred())
    assert ".predictions.lock" in str(ei.value)
    assert memory_store.load_predictions() == []          # nothing was written
    assert lock.exists()                                  # holder's lock untouched


def test_second_writer_waits_for_release():
    lock = _lock_path()
    lock.write_text("pid=held", encoding="utf-8")
    t = threading.Timer(0.25, lock.unlink)
    t.start()
    try:
        p = memory_store.record_prediction(make_pred())   # waits, then succeeds
    finally:
        t.join()
    assert p["pred_id"]
    assert not lock.exists()


def test_stale_lock_is_broken():
    lock = _lock_path()
    lock.write_text("pid=dead", encoding="utf-8")
    old = time.time() - 3600                              # a crashed writer's leftover
    os.utime(lock, (old, old))
    p = memory_store.record_prediction(make_pred())       # broken, then acquired
    assert p["pred_id"]
    assert not lock.exists()


@pytest.mark.parametrize("op", ["record", "resolve", "revise", "add_evidence",
                                "recompute_calibration"])
def test_every_mutating_path_respects_a_held_lock(monkeypatch, op):
    seeded = memory_store.record_prediction(make_pred(market_id="lk"))
    ops = {
        "record": lambda: memory_store.record_prediction(make_pred(market_id="lk2")),
        "resolve": lambda: memory_store.resolve_prediction(seeded["pred_id"], 0),
        "revise": lambda: memory_store.revise(seeded["pred_id"], {"confidence": 0.7}),
        "add_evidence": lambda: memory_store.add_evidence(
            seeded["pred_id"], [{"key_finding": "y"}]),
        "recompute_calibration": memory_store.recompute_calibration,
    }
    monkeypatch.setattr(memory_store, "_LOCK_TIMEOUT", 0.2)
    lock = _lock_path()
    lock.write_text("pid=held", encoding="utf-8")
    with pytest.raises(TimeoutError):
        ops[op]()
    lock.unlink()
