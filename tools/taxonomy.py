"""
tools/taxonomy.py — the ONE keyword taxonomy behind both classifiers.

`polymarket.categorize` (market category: attention triage, caps, backtest
stratification) and `portfolio._factor` (correlated-factor exposure) grew
near-duplicate keyword lists that were guaranteed to drift apart. Both now import
from here, so a keyword added for the scanner automatically informs exposure
grouping and vice versa.

Matching is word-boundary aware where a plain substring was wrong:
  * a keyword that starts alphanumeric gets a leading ``\\b`` — "minister" no
    longer matches "administer", "league" no longer matches "colleague";
  * a keyword written with a trailing space (the old lists' convention for
    "whole word here") gets a trailing ``\\b`` — " war " still refuses "warren";
  * everything else stays open on the right so plurals/derivatives keep matching
    ("sanction" → "sanctions", "poll" → "polling", "crypto" → "cryptocurrency").

Group ORDER is load-bearing: more specific buckets come first (e.g. "Trump vs
Harris" must land in Politics, not Sports, which owns the generic " vs ").
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Market categories (used by polymarket.categorize)
# --------------------------------------------------------------------------- #
CATEGORY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Crypto", [" btc", "bitcoin", "ethereum", " eth ", "solana", " sol ", " xrp",
                "dogecoin", "memecoin", "altcoin", "stablecoin", " crypto", "token",
                "up or down", "binance", "coinbase", "on-chain", "onchain"]),
    ("Politics", ["election", "reelection", "president", "senate", "congress",
                  "governor", "primary", "nominee", "parliament", "prime minister",
                  "minister", "government", "impeach", " vote", "ballot",
                  "candidate", " poll", "referendum", "cabinet", "sworn in",
                  "resign", "approval rating",
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
                 "inflation", "disinflation", " cpi", " gdp", "unemployment",
                 "jobs report", "recession", " ecb", "fomc", "tariff", "yield"]),
    ("Weather", ["temperature", "weather", " rain", " snow", "hurricane", "degrees",
                 "celsius", "fahrenheit", " storm"]),
    ("Culture", ["movie", "box office", "oscar", "grammy", "emmy", "album",
                 "billboard", " song", " award", "rotten tomatoes", "netflix",
                 "spotify", " episode", " season"]),
    ("Sports", [" vs ", " vs.", "match", "rematch", " beat ", "defeat",
                "championship", "playoff", " cup", "league", " nba", " nfl",
                " mlb", " nhl", " ufc", " fight", "soccer", "football", "tennis",
                " golf", " f1", "grand prix", "world cup", "esports",
                "counter-strike", " dota", "valorant", "wins the game", "to win",
                # betting-structure props that carry no team/league noun above and so
                # leak into "Other" — catch them so the efficient-category caps bite.
                "spread:", "exact score", "moneyline", "o/u", "to score",
                "+ goals", "+ goal", "halftime", "half-time", "leading at",
                "strikeouts", "home runs", " win on 20", "draw at"]),
]

# --------------------------------------------------------------------------- #
# Coarse correlated factors (used by portfolio._factor) — markets sharing one
# tend to resolve together.
# --------------------------------------------------------------------------- #
FACTOR_KEYWORDS: list[tuple[str, list[str]]] = [
    ("global-conflict", ["invade", "invasion", " war ", "ceasefire", "regime",
                         "nuclear", "annex", "military", "strike", "airstrike",
                         "air strike", "drone strike", "missile", "troops",
                         "attack"]),
    ("us-rates", ["fed ", "rate cut", "rate hike", "interest rate", "fomc",
                  "inflation", "disinflation", " cpi", "recession"]),
    ("us-politics", ["trump", "biden", "harris", "election", "reelection",
                     "senate", "congress", "president", "nominee", "impeach"]),
    ("crypto-beta", [" btc", "bitcoin", "ethereum", " eth ", "solana", " crypto",
                     "token"]),
]


def _keyword_pattern(keyword: str) -> str:
    """One keyword → a regex fragment with word boundaries where substring is wrong."""
    stripped = keyword.strip().lower()
    pat = re.escape(stripped)
    if stripped[:1].isalnum():
        pat = r"\b" + pat
    if keyword != keyword.rstrip() and stripped[-1:].isalnum():
        pat += r"\b"
    return pat


def _compile(groups: list[tuple[str, list[str]]]) -> list[tuple[str, re.Pattern]]:
    return [(name, re.compile("|".join(_keyword_pattern(k) for k in kws)))
            for name, kws in groups]


_CATEGORY_PATTERNS = _compile(CATEGORY_KEYWORDS)
_FACTOR_PATTERNS = _compile(FACTOR_KEYWORDS)


def _first_match(text: str | None,
                 compiled: list[tuple[str, re.Pattern]]) -> str | None:
    if not text:
        return None
    low = text.lower()
    for name, rx in compiled:
        if rx.search(low):
            return name
    return None


def match_category(text: str | None) -> str | None:
    """First matching market category for a question text, or None."""
    return _first_match(text, _CATEGORY_PATTERNS)


def match_factor(text: str | None) -> str | None:
    """First matching correlated factor for a question text, or None."""
    return _first_match(text, _FACTOR_PATTERNS)
