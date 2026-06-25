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
from datetime import datetime, timezone
from typing import Any

import config


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
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8")


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
    entry.setdefault("pred_id", f"p{len(preds) + 1:05d}")
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


def resolve_prediction(pred_id: str, outcome: int) -> dict[str, Any] | None:
    """Mark a prediction resolved (outcome 1=YES, 0=NO) and score it."""
    from tools.scoring import brier_score

    preds = load_predictions()
    target = None
    for p in preds:
        if p.get("pred_id") == pred_id:
            p["status"] = "resolved"
            p["outcome"] = int(outcome)
            p["resolved_at"] = _now()
            if p.get("model_prob") is not None:
                p["brier"] = round(brier_score(p["model_prob"], outcome), 4)
            target = p
            break
    if target is not None:
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

    resolved = [p for p in load_predictions()
                if p.get("status") == "resolved" and p.get("model_prob") is not None]
    report = calibration_report(resolved)
    report["updated_at"] = _now()
    _save(config.CALIBRATION_FILE, report)
    return report


def load_calibration() -> dict[str, Any]:
    return _load(config.CALIBRATION_FILE, {})
