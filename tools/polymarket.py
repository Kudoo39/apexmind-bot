"""
Polymarket scanner — reads public market data from the Gamma API.

No authentication required for reads. We normalise the messy Gamma payload into
clean dicts and apply ApexMind's liquidity/volume/horizon filters so the briefing
only ever contains tradeable, resolvable markets.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from dateutil import parser as dateparser

import config
from tools import schema_sentinel

_HEADERS = {"User-Agent": "ApexMind/1.0"}


# --------------------------------------------------------------------------- #
# Low-level fetch
# --------------------------------------------------------------------------- #
def _get(path: str, params: dict[str, Any]) -> Any:
    """GET against the Gamma API (market metadata)."""
    url = f"{config.GAMMA_API}{path}"
    resp = requests.get(url, params=params, timeout=30, headers=_HEADERS)
    resp.raise_for_status()
    return resp.json()


def _get_clob(path: str, params: dict[str, Any]) -> Any:
    """GET against the CLOB API (order book / price history)."""
    url = f"{config.CLOB_API}{path}"
    resp = requests.get(url, params=params, timeout=30, headers=_HEADERS)
    resp.raise_for_status()
    return resp.json()


def _parse_json_field(raw: Any) -> Any:
    """Gamma encodes lists like outcomes/prices as JSON *strings*."""
    if isinstance(raw, (list, dict)):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def _to_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _days_until(end_date: str | None) -> float | None:
    if not end_date:
        return None
    try:
        end = dateparser.parse(end_date)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        delta = end - datetime.now(timezone.utc)
        return delta.total_seconds() / 86400.0
    except (ValueError, OverflowError):
        return None


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def _normalise(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Turn a raw Gamma market into ApexMind's clean schema, or None if unusable."""
    outcomes = _parse_json_field(raw.get("outcomes")) or []
    prices = _parse_json_field(raw.get("outcomePrices")) or []

    # We focus on binary YES/NO markets (the bread and butter of forecasting).
    if len(outcomes) != 2 or len(prices) != 2:
        return None

    # Implied probability of YES. Gamma lists the "Yes" outcome first by convention.
    yes_idx = 0
    for i, o in enumerate(outcomes):
        if str(o).strip().lower() in ("yes", "true"):
            yes_idx = i
            break
    yes_prob = _to_float(prices[yes_idx], default=-1.0)
    if not (0.0 <= yes_prob <= 1.0):
        return None

    end_date = raw.get("endDate")
    days_left = _days_until(end_date)

    # CLOB token ids power price-history lookups. Gamma encodes them as a JSON
    # string list, ordered the same as `outcomes`.
    clob_tokens = _parse_json_field(raw.get("clobTokenIds")) or []
    yes_token_id = (str(clob_tokens[yes_idx])
                    if len(clob_tokens) == len(outcomes) and clob_tokens else None)

    return {
        "id": str(raw.get("id", "")),
        "slug": raw.get("slug", ""),
        "question": (raw.get("question") or "").strip(),
        "description": (raw.get("description") or "").strip(),
        "market_prob": round(yes_prob, 4),          # P(YES) implied by the book
        "outcomes": outcomes,
        "yes_index": yes_idx,
        "volume": _to_float(raw.get("volumeNum") or raw.get("volume")),
        "volume_24hr": _to_float(raw.get("volume24hr")),
        "liquidity": _to_float(raw.get("liquidityNum") or raw.get("liquidity")),
        "end_date": end_date,
        "days_to_resolution": round(days_left, 1) if days_left is not None else None,
        "best_bid": _to_float(raw.get("bestBid")),
        "best_ask": _to_float(raw.get("bestAsk")),
        "spread": _to_float(raw.get("spread")),
        "resolution_source": (raw.get("resolutionSource") or "").strip(),
        "category": (raw.get("category") or "").strip(),
        "closed": bool(raw.get("closed")),
        "clob_token_ids": [str(t) for t in clob_tokens] if clob_tokens else [],
        "yes_token_id": yes_token_id,
        "url": f"https://polymarket.com/event/{raw.get('slug', '')}",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------- #
# Category detection — keyword classifier on the question (+ raw category fallback)
# Ordered: more specific buckets first so e.g. "Trump vs Harris" lands in Politics,
# not Sports (which owns the generic " vs ").
# --------------------------------------------------------------------------- #
_CATEGORY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Crypto", [" btc", "bitcoin", "ethereum", " eth ", "solana", " sol ", " xrp",
                "dogecoin", "memecoin", "altcoin", "stablecoin", " crypto", "token",
                "up or down", "binance", "coinbase", "on-chain", "onchain"]),
    ("Politics", ["election", "president", "senate", "congress", "governor",
                  "primary", "nominee", "parliament", "prime minister", "minister",
                  "government", "impeach", " vote", "ballot", "candidate", " poll",
                  "referendum", "cabinet", "sworn in", "resign", "approval rating",
                  # geopolitics & named leaders (high-edge resolution-criteria plays)
                  "invade", "invasion", " war ", "ceasefire", "regime", "sanction",
                  "nuclear", "annex", "secede", "leader of", "coup d", "treaty",
                  " nato", "greenland", "taiwan", "trump", "putin", "zelensky",
                  "netanyahu", "kim jong", "indicted", "pardon", "tariff war",
                  # named conflict actors/theatres that fall through the cracks above
                  "iran", "israel", "gaza", "ukraine", "russia", "hamas",
                  "hezbollah", "houthi", "north korea", "venezuela", "missile",
                  "airstrike", "air strike", "drone strike", "hostage", "occupy",
                  " control by", " control of", "military"]),
    ("Economy", ["fed ", "federal reserve", "interest rate", "rate cut", "rate hike",
                 "inflation", " cpi", " gdp", "unemployment", "jobs report",
                 "recession", " ecb", "fomc", "tariff", "yield"]),
    ("Weather", ["temperature", "weather", " rain", " snow", "hurricane", "degrees",
                 "celsius", "fahrenheit", " storm"]),
    ("Culture", ["movie", "box office", "oscar", "grammy", "emmy", "album",
                 "billboard", " song", " award", "rotten tomatoes", "netflix",
                 "spotify", " episode", " season"]),
    ("Sports", [" vs ", " vs.", "match", " beat ", "defeat", "championship",
                "playoff", " cup", "league", " nba", " nfl", " mlb", " nhl",
                " ufc", " fight", "soccer", "football", "tennis", " golf", " f1",
                "grand prix", "world cup", "esports", "counter-strike", " dota",
                "valorant", "wins the game", "to win",
                # betting-structure props that carry no team/league noun above and so
                # leak into "Other" — catch them so the efficient-category caps bite.
                "spread:", "exact score", "moneyline", "o/u", "to score",
                "+ goals", "+ goal", "halftime", "half-time", "leading at",
                "strikeouts", "home runs", " win on 20", "draw at"]),
]


def categorize(market: dict) -> str:
    """Best-effort market category from the question text (raw `category` as fallback)."""
    q = " " + (market.get("question") or "").lower() + " "
    for cat, kws in _CATEGORY_KEYWORDS:
        if any(k in q for k in kws):
            return cat
    raw = (market.get("category") or "").strip()
    return raw.title()[:16] if raw else "Other"


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def scan_markets(limit: int | None = None) -> list[dict[str, Any]]:
    """Fetch active, open, binary markets ordered by total volume (edge-rich first).

    Gamma caps the /markets page at ~100 rows, so we PAGE via offset up to `limit` to
    reach the mid-volume tier where near-term Politics/Regulatory/Macro markets live
    (the top-100-by-volume slice is dominated by marquee year-end + outright markets, so
    a single page never surfaces e.g. Fed-rate or midterm-control markets 30-180d out).
    """
    limit = limit or config.SCAN_LIMIT
    page = 100                                    # Gamma's effective page cap
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for off in range(0, max(limit, 1), page):
        try:
            raw = _get("/markets", {
                "active": "true", "closed": "false", "archived": "false",
                "limit": page, "offset": off,
                "order": config.SCAN_ORDER, "ascending": "false",
            })
        except requests.RequestException:
            break
        if off == 0:
            schema_sentinel.check("gamma_markets", raw)
        if not isinstance(raw, list) or not raw:
            break
        added = 0
        for r in raw:
            mid = str(r.get("id"))
            if mid in seen:
                continue
            seen.add(mid)
            added += 1
            m = _normalise(r)
            if m and m["question"]:
                out.append(m)
        if added == 0:                            # offset not honoured / pool exhausted
            break
    return out


def _is_intraday(m: dict[str, Any]) -> bool:
    """True for intraday / coin-flip markets we deliberately refuse to analyse."""
    q = (m.get("question") or "").lower()
    if any(p in q for p in config.EXCLUDE_PATTERNS):
        return True
    d = m.get("days_to_resolution")
    return d is not None and d < config.MIN_DAYS_TO_RESOLUTION


def shortlist(markets: list[dict[str, Any]],
              size: int | None = None) -> list[dict[str, Any]]:
    """Edge-aware shortlist.

    Drops coin-flips and intraday churn, then ranks by *edge potential* rather than
    raw uncertainty: high-edge categories (Politics/Macro — resolution-criteria &
    procedure plays) are favoured, while short-dated crypto/sports/weather are
    de-prioritised AND capped so they can never dominate the briefing. The crowd is
    efficient on those (backtested Brier ~0.24); our edge lives elsewhere.
    """
    size = size or config.SHORTLIST_SIZE
    candidates = []
    for m in markets:
        if m["liquidity"] < config.MIN_LIQUIDITY or m["volume"] < config.MIN_VOLUME:
            continue
        if not (config.SHORTLIST_PROB_LO <= m["market_prob"] <= config.SHORTLIST_PROB_HI):
            continue                                # skip near-mechanical extremes
        d = m["days_to_resolution"]
        if d is None or d < config.MIN_DAYS_TO_RESOLUTION or d > config.MAX_DAYS_TO_RESOLUTION:
            continue
        if _is_intraday(m):
            continue
        m["category"] = categorize(m)
        candidates.append(m)

    def score(m: dict[str, Any]) -> float:
        prio = config.CATEGORY_PRIORITY.get(m["category"], 1.0)
        # Reward the analysable band but DON'T peak at the 0.50 coin-flip.
        analysable = 1.0 - abs(m["market_prob"] - 0.5)          # 0.5 … 1.0
        d = m["days_to_resolution"] or 0
        horizon_fit = 1.0 if 2 <= d <= 180 else 0.7             # full weight for the 30-180d sweet spot
        return prio * analysable * horizon_fit * (1.0 + m["volume_24hr"] ** 0.0001)

    candidates.sort(key=score, reverse=True)

    out: list[dict[str, Any]] = []
    caps: dict[str, int] = {}
    for m in candidates:
        c = m["category"]
        cap = config.CATEGORY_CAPS.get(c)
        if cap is not None and caps.get(c, 0) >= cap:
            continue
        caps[c] = caps.get(c, 0) + 1
        out.append(m)
        if len(out) >= size:
            break
    return out


def fetch_and_shortlist() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convenience: scan everything, return (all_markets, shortlisted)."""
    everything = scan_markets()
    return everything, shortlist(everything)


# --------------------------------------------------------------------------- #
# Single-market lookup & resolution status (auto-resolver)
# --------------------------------------------------------------------------- #
def get_market_by_id(market_id: str) -> dict[str, Any] | None:
    """Fetch one market's raw Gamma payload by id (works for closed markets too)."""
    try:
        data = _get(f"/markets/{market_id}", {})
    except requests.RequestException:
        return None
    if isinstance(data, list):
        data = data[0] if data else None
    return data if isinstance(data, dict) else None


def check_resolution(market_id: str) -> dict[str, Any]:
    """Determine whether a market has resolved and, if so, its YES/NO outcome.

    Returns a dict:
      {market_id, found, closed, resolved, outcome (1=YES,0=NO,None=unclear),
       yes_price, question}
    A market is treated as *resolved* only when it is closed AND its final YES
    price is unambiguous (≈1 or ≈0). Anything in between is left for a human.
    """
    raw = get_market_by_id(market_id)
    if raw is None:
        return {"market_id": market_id, "found": False, "closed": False,
                "resolved": False, "outcome": None, "yes_price": None,
                "question": None}

    outcomes = _parse_json_field(raw.get("outcomes")) or []
    prices = _parse_json_field(raw.get("outcomePrices")) or []
    yes_idx = 0
    for i, o in enumerate(outcomes):
        if str(o).strip().lower() in ("yes", "true"):
            yes_idx = i
            break
    yes_price = _to_float(prices[yes_idx], default=-1.0) if len(prices) > yes_idx else -1.0

    closed = bool(raw.get("closed"))
    # Some markets also expose an explicit UMA resolution status.
    uma = str(raw.get("umaResolutionStatus") or "").lower()
    looks_resolved = closed or uma == "resolved"

    outcome: int | None = None
    if looks_resolved and 0.0 <= yes_price <= 1.0:
        if yes_price >= 0.99:
            outcome = 1
        elif yes_price <= 0.01:
            outcome = 0

    return {
        "market_id": market_id,
        "found": True,
        "closed": closed,
        "resolved": outcome is not None,
        "outcome": outcome,
        "yes_price": round(yes_price, 4) if yes_price >= 0 else None,
        "question": (raw.get("question") or "").strip(),
    }


# --------------------------------------------------------------------------- #
# Resolved-market fetch (backtesting)
# --------------------------------------------------------------------------- #
def prepare_resolved(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise a raw Gamma market and tag its settled outcome.

    Returns the clean market dict with extra keys `outcome` (1=YES, 0=NO),
    `end_ts` (unix) and `_end` (datetime), or None if it isn't a cleanly-resolved
    binary market (ambiguous/refunded/missing data are skipped).
    """
    m = _normalise(raw)
    if not m or not m["question"] or not m["yes_token_id"]:
        return None
    p = m["market_prob"]                      # final settlement price
    if p >= 0.99:
        m["outcome"] = 1
    elif p <= 0.01:
        m["outcome"] = 0
    else:
        return None                            # never cleanly resolved

    # `closedTime` is when the market actually resolved (can differ from the nominal
    # endDate — some markets close early). Prefer it; fall back to endDate.
    stamp = raw.get("closedTime") or m.get("end_date")
    if not stamp:
        return None
    try:
        end = dateparser.parse(str(stamp))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        return None
    m["resolved_at"] = end.isoformat()
    m["_end"] = end
    m["end_ts"] = int(end.timestamp())

    # Nominal lifetime from createdAt — a cheap pre-filter to skip ultra-short
    # (e.g. 5-minute "up or down") markets before spending a CLOB history call.
    created = raw.get("createdAt")
    m["created_ts"] = None
    if created:
        try:
            cdt = dateparser.parse(str(created))
            if cdt.tzinfo is None:
                cdt = cdt.replace(tzinfo=timezone.utc)
            m["created_ts"] = int(cdt.timestamp())
        except (ValueError, OverflowError):
            pass
    m["nominal_life_secs"] = (m["end_ts"] - m["created_ts"]) if m["created_ts"] else None
    return m


def fetch_resolved_markets(days: int = 90, limit: int = 200,
                           min_volume: float | None = None,
                           categories: list[str] | None = None,
                           per_category_min: int = 0,
                           category_caps: dict[str, int] | None = None,
                           order: str = "closedTime",
                           pages: int | None = None) -> list[dict[str, Any]]:
    """Fetch cleanly-resolved, *traded* binary markets for the backtest.

    Filters out zero-volume junk and obviously ultra-short markets so the backtest
    only spends CLOB history calls on markets worth scoring.

    **Ordering is the load-bearing knob.** The default Gamma sort, ``closedTime``
    (most-recently-resolved), is dominated by the high-cardinality, fast-churning
    Sports/Weather/Crypto/daily coin-flips that resolve constantly — empirically the
    top 500 closed-by-recency markets contain ~0 Politics/Economy, so the recency pull
    is *structurally blind* to the niches our edge thesis depends on. Passing
    ``order="volumeNum"`` instead surfaces the marquee, high-volume resolutions — the
    same markets the live scanner triages (``config.SCAN_ORDER``) and the ones we would
    actually take positions on. Over a 90-day window that flips the thesis share from
    ~5% to ~85% (Politics+Economy). **Always backtest with volume ordering.**

    **Category stratification** (active when any of `categories`, `per_category_min`,
    or `category_caps` is supplied) is a safety net on top of the ordering:
      1. **cap** the efficient coin-flip categories (``category_caps``) so a burst of
         them can never swamp the sample;
      2. **guarantee** at least ``per_category_min`` markets for each named category;
      3. fill the remaining slots in the pool's native (volume or recency) order.

    With none of those args it preserves the original single-page behaviour exactly.
    The pool walk dedupes by id, so it is robust even where Gamma ignores ``offset``
    (it does on the closed-markets endpoint — deep paging is effectively a no-op, which
    is why correct *ordering*, not deeper paging, is what unlocks the thesis markets).
    """
    min_volume = config.MIN_VOLUME if min_volume is None else min_volume
    category_caps = category_caps or {}
    stratify = bool(categories or category_caps or per_category_min)
    n_pages = max(1, pages if pages is not None else 1)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    # 1. Pull the (deduped, optionally paged) pool of resolved, traded markets.
    prepared: list[dict[str, Any]] = []
    seen_pool: set[str] = set()
    for pg in range(n_pages):
        try:
            chunk = _get("/markets", {
                "closed": "true", "archived": "false", "limit": 500,
                "offset": pg * 500,
                "order": order, "ascending": "false",
                "volume_num_min": min_volume,
            })
        except requests.RequestException:
            # Gamma caps how deep `offset` may page (422 past the limit); keep
            # whatever earlier pages returned rather than failing the run.
            break
        if not isinstance(chunk, list) or not chunk:
            break
        schema_sentinel.check("resolved_market", chunk)
        added = 0
        for r in chunk:
            m = prepare_resolved(r)
            if not m or m["id"] in seen_pool:
                continue
            if m["_end"] < cutoff or m["_end"] > now:
                continue
            if m.get("volume", 0.0) < min_volume:
                continue
            nl = m.get("nominal_life_secs")
            if nl is not None and nl < 3600:          # skip < 1h markets up front
                continue
            seen_pool.add(m["id"])
            m["category"] = categorize(m)
            prepared.append(m)
            added += 1
            if not stratify and len(prepared) >= limit:
                return prepared
        if added == 0:                                # a page of all-dupes ends the walk
            break

    if not stratify:
        return prepared[:limit]

    # 2. Apply per-category caps in pool order (so the efficient coin-flip categories
    #    can't dominate), keeping the global volume/recency ranking intact.
    cat_seen: dict[str, int] = {}
    capped: list[dict[str, Any]] = []
    for m in prepared:
        cat = m["category"]
        cat_seen[cat] = cat_seen.get(cat, 0) + 1
        cap = category_caps.get(cat)
        if cap is not None and cat_seen[cat] > cap:
            continue
        capped.append(m)

    # 3. Guarantee the per-category minimum for each named target category first...
    out: list[dict[str, Any]] = []
    taken: set[str] = set()
    if per_category_min:
        by_cat: dict[str, list[dict[str, Any]]] = {}
        for m in capped:
            by_cat.setdefault(m["category"], []).append(m)
        for cat in (categories or []):
            for m in by_cat.get(cat, [])[:per_category_min]:
                if m["id"] not in taken:
                    taken.add(m["id"])
                    out.append(m)

    # 4. ...then fill the remaining slots in the pool's native order, up to `limit`.
    for m in capped:
        if len(out) >= limit:
            break
        if m["id"] in taken:
            continue
        taken.add(m["id"])
        out.append(m)
    return out


# --------------------------------------------------------------------------- #
# Price history (CLOB time-series) for richer briefings
# --------------------------------------------------------------------------- #
def price_history_range(token_id: str, start_ts: int, end_ts: int,
                        fidelity_minutes: int = 360) -> list[dict[str, float]]:
    """YES-price history for a CLOB token over an explicit [start_ts, end_ts] window.

    Returns [{"t": unix_seconds, "p": price}] ascending in time; [] on any failure.
    Used by the backtest harness to read prices *as they were* before resolution.
    """
    if not token_id:
        return []
    try:
        data = _get_clob("/prices-history", {
            "market": token_id,
            "startTs": int(start_ts),
            "endTs": int(end_ts),
            "fidelity": fidelity_minutes,
        })
    except requests.RequestException:
        return []
    schema_sentinel.check("clob_prices_history", data)
    hist = data.get("history") if isinstance(data, dict) else None
    if not isinstance(hist, list):
        return []
    out = []
    for pt in hist:
        t = pt.get("t")
        p = pt.get("p")
        if t is None or p is None:
            continue
        out.append({"t": int(t), "p": _to_float(p)})
    out.sort(key=lambda x: x["t"])
    return out


def _parse_history(data: Any) -> list[dict[str, float]]:
    hist = data.get("history") if isinstance(data, dict) else None
    if not isinstance(hist, list):
        return []
    out = []
    for pt in hist:
        t, p = pt.get("t"), pt.get("p")
        if t is None or p is None:
            continue
        out.append({"t": int(t), "p": _to_float(p)})
    out.sort(key=lambda x: x["t"])
    return out


def price_history_full(token_id: str,
                       fidelity_minutes: int = 60) -> list[dict[str, float]]:
    """The *entire* price history of a CLOB token via interval=max.

    The CLOB rejects explicit startTs/endTs windows wider than ~2 weeks (HTTP 400),
    so for backtesting whole-life series we use interval=max instead. [] on failure.
    """
    if not token_id:
        return []
    try:
        data = _get_clob("/prices-history",
                         {"market": token_id, "interval": "max",
                          "fidelity": fidelity_minutes})
    except requests.RequestException:
        return []
    schema_sentinel.check("clob_prices_history", data)
    return _parse_history(data)


def get_price_history(token_id: str, days: int = 7,
                      fidelity_minutes: int = 360) -> list[dict[str, float]]:
    """Recent YES-price history for a CLOB token (last `days`). Best-effort; [] on failure."""
    now = int(time.time())
    return price_history_range(token_id, now - days * 86400, now, fidelity_minutes)


_SPARK = "▁▂▃▄▅▆▇█"


def _sparkline(prices: list[float]) -> str:
    """Tiny unicode sparkline, auto-scaled to the observed min..max band."""
    if not prices:
        return ""
    lo, hi = min(prices), max(prices)
    span = hi - lo
    if span < 1e-9:
        return _SPARK[3] * len(prices)
    return "".join(_SPARK[min(int((p - lo) / span * 7), 7)] for p in prices)


def summarize_history(history: list[dict[str, float]],
                      max_points: int = 7) -> dict[str, Any] | None:
    """Condense a raw price series into a compact, briefing-friendly summary."""
    if not history:
        return None
    prices = [h["p"] for h in history]
    first, last = prices[0], prices[-1]

    # Evenly downsample to <= max_points for a readable trail.
    n = len(history)
    if n <= max_points:
        sample = history
    else:
        step = (n - 1) / (max_points - 1)
        sample = [history[round(i * step)] for i in range(max_points)]

    points = []
    for h in sample:
        d = datetime.fromtimestamp(h["t"], tz=timezone.utc).strftime("%m-%d")
        points.append([d, round(h["p"], 3)])

    return {
        "first": round(first, 4),
        "last": round(last, 4),
        "min": round(min(prices), 4),
        "max": round(max(prices), 4),
        "change": round(last - first, 4),
        "n": n,
        "sparkline": _sparkline([h["p"] for h in sample]),
        "points": points,
    }


def attach_price_history(markets: list[dict[str, Any]],
                         days: int | None = None) -> list[dict[str, Any]]:
    """Best-effort: attach a `history` summary to each market in-place.

    Failures are swallowed per-market so a flaky CLOB endpoint never breaks a scan.
    """
    days = days or config.HISTORY_DAYS
    for m in markets:
        try:
            raw = get_price_history(m.get("yes_token_id"), days=days)
            m["history"] = summarize_history(raw)
        except Exception:  # noqa: BLE001 — enrichment must never be fatal
            m["history"] = None
    return markets


if __name__ == "__main__":
    allm, short = fetch_and_shortlist()
    print(f"Scanned {len(allm)} binary markets, shortlisted {len(short)}.")
    for m in short:
        print(f"  [{m['market_prob']:.0%}] {m['question'][:70]}  "
              f"(liq=${m['liquidity']:,.0f}, {m['days_to_resolution']}d)")
