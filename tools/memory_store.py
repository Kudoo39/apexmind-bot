"""
File-based long-term memory for ApexMind.

Everything is plain JSON / Markdown so it is:
  * inspectable & diff-able in git,
  * directly readable by Claude Code in a session,
  * trivially editable by hand.

Memory files
------------
  beliefs.json      structural world-model beliefs (with confidence)
  predictions.json  the track record — one entry per Specialist/Supervisor call
  calibration.json  bucketed calibration counters (derived, but cached)
  lessons.md        narrative if-then lessons from the Reflection role
"""

from __future__ import annotations

import json
import math
import warnings
from datetime import datetime, timezone
from typing import Any

import config
from tools import atomic_io


# --------------------------------------------------------------------------- #
# Generic JSON helpers
# --------------------------------------------------------------------------- #
def _load(path, default):
    if not path.exists():
        return default
    try:
        # utf-8-sig tolerates a BOM if the file was hand-edited on Windows.
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return default


def _save(path, data) -> None:
    # Atomic write: a crash / full disk mid-write must never truncate the
    # source-of-truth book (or calibration / beliefs). See tools/atomic_io.py.
    atomic_io.atomic_write_json(path, data)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Beliefs
# --------------------------------------------------------------------------- #
def load_beliefs() -> list[dict[str, Any]]:
    return _load(config.BELIEFS_FILE, {"beliefs": []}).get("beliefs", [])


def save_beliefs(beliefs: list[dict[str, Any]]) -> None:
    _save(config.BELIEFS_FILE, {"updated_at": _now(), "beliefs": beliefs})


def relevant_beliefs(categories: list[str]) -> list[dict[str, Any]]:
    """Beliefs whose optional `category` matches `categories`, plus all general
    (uncategorised) beliefs — those always apply."""
    wanted = {c.lower() for c in categories}
    out = []
    for b in load_beliefs():
        cat = b.get("category")
        if not cat or str(cat).lower() in wanted:
            out.append(b)
    return out


# --------------------------------------------------------------------------- #
# Predictions (the track record)
# --------------------------------------------------------------------------- #
def load_predictions() -> list[dict[str, Any]]:
    return _load(config.PREDICTIONS_FILE, {"predictions": []}).get("predictions", [])


def save_predictions(preds: list[dict[str, Any]]) -> None:
    _save(config.PREDICTIONS_FILE, {"updated_at": _now(), "predictions": preds})


_EVIDENCE_TIERS = ("primary", "expert", "market", "social", "unspecified")


def _normalise_evidence(evidence: Any) -> list[dict[str, Any]]:
    """Clean a raw evidence list into the ledger schema.

    Each entry is one piece of research that moved (or confirmed) the estimate:
      {query, source_url, source_tier, key_finding, likelihood_ratio,
       direction, fetched_at}
    `likelihood_ratio` is the multiplicative LR the Specialist applied (1.0 = neutral
    context, >1 supports YES, <1 supports NO); None means "context only, no update".
    """
    if not isinstance(evidence, list):
        return []
    out = []
    for e in evidence:
        if not isinstance(e, dict):
            continue
        tier = str(e.get("source_tier", "unspecified")).lower()
        out.append({
            "query": e.get("query", ""),
            "source_url": e.get("source_url", ""),
            "source_tier": tier if tier in _EVIDENCE_TIERS else "unspecified",
            "key_finding": e.get("key_finding", ""),
            "likelihood_ratio": e.get("likelihood_ratio"),
            "direction": e.get("direction"),          # YES | NO | None
            "fetched_at": e.get("fetched_at") or _now(),
        })
    return out


def _backfill_from_cache(entry: dict[str, Any]) -> None:
    """Fill market fields from the cached shortlist when only an id was given, so
    EVERY write path (CLI, skill, direct script) yields a complete row. The portfolio
    factor classifier keys off the question text — a blank question silently mis-buckets
    the position (e.g. a Fed market filed under 'other' instead of 'us-rates')."""
    mid = entry.get("market_id")
    if not mid:
        return
    cache = _load(config.MARKETS_CACHE, {"shortlist": []})
    for m in cache.get("shortlist", []):
        if m.get("id") == str(mid):
            entry.setdefault("question", m.get("question"))
            if entry.get("market_prob") is None:
                entry["market_prob"] = m.get("market_prob")
            entry.setdefault("end_date", m.get("end_date"))
            entry.setdefault("url", m.get("url"))
            entry.setdefault("liquidity", m.get("liquidity"))
            entry.setdefault("category", m.get("category"))
            break


def _next_pred_id(preds: list[dict[str, Any]]) -> str:
    """Next collision-proof prediction id: max numeric `pXXXXX` + 1.

    NOT `len(preds)+1`: the README invites hand-editing, and deleting any row would
    make a count-based id collide with an existing one (two rows sharing a pred_id —
    every resolve/add_evidence/reflect lookup then silently hits the wrong row).
    Positional-but-monotonic survives deletions and voids. Non-`p` ids (e.g. the
    backtest's `bt-…`) are ignored.
    """
    mx = 0
    for p in preds:
        pid = str(p.get("pred_id") or "")
        if pid.startswith("p"):
            try:
                mx = max(mx, int(pid[1:]))
            except ValueError:
                continue
    return f"p{mx + 1:05d}"


def record_prediction(entry: dict[str, Any]) -> dict[str, Any]:
    """Append a new prediction. Returns the stored entry (with id + timestamps).

    Expected keys (the reasoning layer fills these in):
      market_id, question, market_prob, model_prob, confidence,
      decision (POSITION/PASS), direction (YES/NO), conviction (1-5),
      rationale, key_uncertainty, half_life, end_date, url
      prior_prob   — the base-rate prior before evidence (optional but encouraged)
      evidence     — the evidence ledger (REQUIRED for POSITIONs); see below
    """
    preds = load_predictions()
    entry = dict(entry)
    _backfill_from_cache(entry)
    # Dedup guard: the append-only store has no dedup by market_id. Re-recording the
    # same OPEN market is almost always a re-analysis, not a second independent bet; a
    # fresh row double-counts conviction in `portfolio` and double-scores calibration.
    # WARN and tag `supersedes` — never raise (a library raise would break skill/script
    # callers). Pass allow_duplicate=True for a genuinely distinct second position.
    mid = entry.get("market_id")
    allow_dup = entry.pop("allow_duplicate", False)
    if mid is not None and not allow_dup:
        dupes = [p for p in preds
                 if str(p.get("market_id")) == str(mid) and p.get("status") == "open"]
        if dupes:
            entry["supersedes"] = [p["pred_id"] for p in dupes]
            warnings.warn(
                f"market_id {mid} already has OPEN prediction(s) {entry['supersedes']}; "
                f"appending a NEW row double-counts it in portfolio/calibration. Prefer an "
                f"in-place edit of memory/predictions.json or "
                f"add_evidence('{dupes[-1]['pred_id']}', [...]). "
                f"Pass allow_duplicate=True only for a genuinely separate position.",
                stacklevel=2)
    entry.setdefault("pred_id", _next_pred_id(preds))
    entry.setdefault("created_at", _now())
    entry.setdefault("status", "open")        # open | resolved
    entry.setdefault("outcome", None)         # 1=YES, 0=NO once resolved
    entry.setdefault("brier", None)
    entry.setdefault("prior_prob", None)      # base rate before evidence
    entry["evidence"] = _normalise_evidence(entry.get("evidence", []))
    if entry.get("market_prob") is not None and entry.get("model_prob") is not None:
        entry["edge"] = round(entry["model_prob"] - entry["market_prob"], 4)
    preds.append(entry)
    save_predictions(preds)
    return entry


def suggest_confidence_ceiling(liquidity: float | None,
                               evidence: list[dict[str, Any]] | None) -> float:
    """Heuristic upper bound on a defensible `confidence` for a call.

    Lower the ceiling when (a) the market is **thin** (an unreliable price you can't
    cleanly act on) and (b) the estimate **leans on low-tier sources** (social/market
    chatter rather than primary/expert). Used as a soft advisory at record time —
    it never overrides the Specialist, it just flags over-confidence.
    """
    ceiling = 0.90
    liq = liquidity or 0.0
    if liq < 20_000:
        ceiling = min(ceiling, 0.75)
    if liq < 5_000:
        ceiling = min(ceiling, 0.65)

    # Of the entries that actually MOVED the estimate (LR present and != 1), how many
    # were low-tier? Heavy social/market reliance caps confidence hard.
    movers = [e for e in (evidence or [])
              if e.get("likelihood_ratio") not in (None, 1, 1.0)]
    if not movers:
        ceiling = min(ceiling, 0.60)          # nothing concrete moved it
    else:
        low = sum(1 for e in movers
                  if e.get("source_tier") in ("social", "market", "unspecified"))
        frac_low = low / len(movers)
        if frac_low >= 0.5:
            ceiling = min(ceiling, 0.65)
        elif frac_low > 0:
            ceiling = min(ceiling, 0.78)
    return round(ceiling, 2)


def reconcile_ledger(entry: dict[str, Any],
                     tol_logodds: float = 0.50) -> dict[str, Any] | None:
    """Check the ledger identity  logit(model) ≈ logit(prior) + Σ ln(LR).

    Pure arithmetic advisory — it NEVER edits the entry or blocks recording (mirrors
    suggest_confidence_ceiling). Returns None when there isn't enough to check (no
    prior, no model, degenerate 0/1, or no usable LRs). `ok` is True when
    |recorded_logit − implied_logit| ≤ tol_logodds (~0.50 ≈ a 1.6x LR of unexplained
    drift — generous, so honest rounding or a small stated shade never trips it).
    """
    prior, model = entry.get("prior_prob"), entry.get("model_prob")
    if prior in (None, 0, 1) or model in (None, 0, 1):
        return None
    lrs = [e.get("likelihood_ratio") for e in entry.get("evidence", [])
           if isinstance(e.get("likelihood_ratio"), (int, float))
           and not isinstance(e.get("likelihood_ratio"), bool)
           and e.get("likelihood_ratio") > 0]
    if not lrs:
        return None
    sum_ln = sum(math.log(lr) for lr in lrs)
    implied_logit = math.log(prior / (1 - prior)) + sum_ln
    recorded_logit = math.log(model / (1 - model))
    gap = recorded_logit - implied_logit
    return {"ok": abs(gap) <= tol_logodds,
            "prior_prob": prior, "model_prob": model,
            "sum_ln_lr": round(sum_ln, 3),
            "implied_model_prob": round(1 / (1 + math.exp(-implied_logit)), 3),
            "gap": round(gap, 3)}


def add_evidence(pred_id: str, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Append more evidence to an existing prediction (e.g. a later research pass)."""
    preds = load_predictions()
    target = None
    for p in preds:
        if p.get("pred_id") == pred_id:
            p.setdefault("evidence", [])
            p["evidence"].extend(_normalise_evidence(entries))
            target = p
            break
    if target is not None:
        save_predictions(preds)
    return target


def open_predictions() -> list[dict[str, Any]]:
    return [p for p in load_predictions() if p.get("status") == "open"]


def superseded_ids(preds: list[dict[str, Any]] | None = None) -> set[str]:
    """Pred ids that a LATER row supersedes.

    The dedup guard in `record_prediction` tags a re-analysis of an already-open
    market with `supersedes: [<old id>]`. Those old rows are the SAME underlying bet
    and must be excluded everywhere they'd otherwise be double-counted — exposure
    (`portfolio`) and the calibration / track-record stats (`scoring`). This is the
    one shared definition both call, so they can never drift apart.
    """
    preds = load_predictions() if preds is None else preds
    out: set[str] = set()
    for p in preds:
        for pid in (p.get("supersedes") or []):
            if pid:
                out.add(str(pid))
    return out


def resolved_predictions(include_superseded: bool = False) -> list[dict[str, Any]]:
    """Canonical resolved track record for scoring.

    Resolved rows that carry a `model_prob`, with superseded re-analyses dropped by
    default (so a revisited bet is scored once). Pass include_superseded=True for the
    raw set.
    """
    preds = load_predictions()
    rows = [p for p in preds
            if p.get("status") == "resolved" and p.get("model_prob") is not None]
    if include_superseded:
        return rows
    sup = superseded_ids(preds)
    return [p for p in rows if str(p.get("pred_id")) not in sup]


def resolve_prediction(pred_id: str, outcome: int) -> dict[str, Any] | None:
    """Mark a prediction resolved (outcome 1=YES, 0=NO) and score it.

    Refuses to re-resolve an already-resolved row: the book is append-only and a
    settled outcome/Brier is final. (`auto-resolve` only ever iterates OPEN rows, so
    it never trips this.) Returns None if no row matches; raises ValueError on a
    re-resolve attempt.
    """
    from tools.scoring import brier_score

    preds = load_predictions()
    target = None
    for p in preds:
        if p.get("pred_id") == pred_id:
            target = p
            break
    if target is None:
        return None
    if target.get("status") == "resolved":
        raise ValueError(
            f"{pred_id} is already resolved (outcome={target.get('outcome')}, "
            f"brier={target.get('brier')}); the book is append-only — refusing to "
            f"re-resolve. If the first resolution was genuinely wrong, hand-edit that "
            f"row in memory/predictions.json, then re-run `status`.")
    target["status"] = "resolved"
    target["outcome"] = int(outcome)
    target["resolved_at"] = _now()
    if target.get("model_prob") is not None:
        target["brier"] = round(brier_score(target["model_prob"], outcome), 4)
    save_predictions(preds)
    return target


# Identity / lifecycle / resolution fields a revise() must never touch — the book is
# append-only and a resolution is final. Everything else (model_prob, confidence,
# decision, direction, conviction, rationale, key_uncertainty, evidence, …) is fair
# game on an OPEN row.
_PROTECTED_FIELDS = frozenset({
    "pred_id", "created_at", "status", "supersedes",
    "outcome", "brier", "resolved_at",
})


def revise(pred_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    """Safely edit an OPEN prediction in place — the sanctioned alternative to a raw
    hand-edit of memory/predictions.json.

    Refuses to touch any protected identity/lifecycle/resolution field, and refuses to
    revise a resolved row at all. Re-derives `edge` when model/market prob change and
    normalises any patched evidence. Returns the updated row, or None if not found;
    raises ValueError on a protected field or a resolved row, TypeError on a non-dict
    patch.
    """
    if not isinstance(patch, dict):
        raise TypeError("patch must be a dict of {field: new_value}")
    blocked = _PROTECTED_FIELDS & set(patch)
    if blocked:
        raise ValueError(
            f"refusing to revise protected field(s) {sorted(blocked)} — these are "
            f"identity/lifecycle/resolution fields. Only an OPEN row's analysis fields "
            f"(model_prob, confidence, decision, direction, conviction, rationale, "
            f"key_uncertainty, half_life, prior_prob, market_prob, evidence) may be revised.")
    preds = load_predictions()
    target = None
    for p in preds:
        if p.get("pred_id") == pred_id:
            target = p
            break
    if target is None:
        return None
    if target.get("status") == "resolved":
        raise ValueError(
            f"{pred_id} is resolved; the book is append-only — a settled row cannot "
            f"be revised.")
    patch = dict(patch)
    if "evidence" in patch:
        patch["evidence"] = _normalise_evidence(patch["evidence"])
    target.update(patch)
    if target.get("market_prob") is not None and target.get("model_prob") is not None:
        target["edge"] = round(target["model_prob"] - target["market_prob"], 4)
    save_predictions(preds)
    return target


# --------------------------------------------------------------------------- #
# Lessons (Markdown, append-only narrative; optionally category-tagged)
# --------------------------------------------------------------------------- #
import re as _re

# Matches "- [2026-06-24] (Politics) text"  or  "- [seed] text"
_LESSON_RE = _re.compile(r"^- \[(?P<date>[^\]]+)\]\s*(?:\((?P<cat>[A-Za-z/ ]+)\)\s*)?"
                         r"(?P<text>.+)$")


def append_lesson(text: str, category: str | None = None) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tag = f"({category.strip().title()}) " if category else ""
    line = f"- [{stamp}] {tag}{text.strip()}\n"
    with config.LESSONS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line)


def load_lessons() -> str:
    if config.LESSONS_FILE.exists():
        return config.LESSONS_FILE.read_text(encoding="utf-8")
    return ""


def load_lessons_structured() -> list[dict[str, Any]]:
    """Parse lesson lines into {date, category, text, raw}. category is None when
    the lesson is general/untagged (always relevant)."""
    out = []
    for raw in load_lessons().splitlines():
        line = raw.rstrip()
        if not line.startswith("- ["):
            continue
        m = _LESSON_RE.match(line)
        if not m:
            out.append({"date": None, "category": None, "text": line[2:].strip(),
                        "raw": line})
            continue
        cat = m.group("cat")
        out.append({"date": m.group("date"),
                    "category": cat.strip() if cat else None,
                    "text": m.group("text").strip(), "raw": line})
    return out


def relevant_lessons(categories: list[str]) -> list[dict[str, Any]]:
    """Lessons whose category matches one of `categories`, plus all general
    (untagged) lessons — those always apply."""
    wanted = {c.lower() for c in categories}
    out = []
    for lsn in load_lessons_structured():
        cat = lsn.get("category")
        if cat is None or cat.lower() in wanted:
            out.append(lsn)
    return out


# --------------------------------------------------------------------------- #
# Calibration (recomputed from resolved predictions)
# --------------------------------------------------------------------------- #
def recompute_calibration() -> dict[str, Any]:
    from tools.scoring import calibration_report

    # resolved_predictions() drops superseded re-analyses so a revisited bet isn't
    # double-scored (which would bias calibration toward oft-revisited high-conviction
    # markets) — the same exclusion portfolio applies to exposure.
    report = calibration_report(resolved_predictions())
    report["updated_at"] = _now()
    _save(config.CALIBRATION_FILE, report)
    return report


def load_calibration() -> dict[str, Any]:
    return _load(config.CALIBRATION_FILE, {})
