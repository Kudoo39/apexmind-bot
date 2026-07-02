"""P2-7 — one shared keyword taxonomy (tools/taxonomy.py) behind both
polymarket.categorize (category) and portfolio._factor (correlated factor), with
word-boundary matching where a plain substring was wrong ("minister" matched
"administer", "league" matched "colleague"). Assertions run through the two
consumers so category/factor behaviour is pinned where it is actually used."""

from __future__ import annotations

import pytest

from tools import polymarket, portfolio, taxonomy


def _cat(question, raw_category=""):
    return polymarket.categorize({"question": question, "category": raw_category})


def _fac(question, category=None):
    return portfolio._factor({"question": question, "category": category})


# --------------------------------------------------------------------------- #
# Previous false positives — the reason word boundaries exist
# --------------------------------------------------------------------------- #
def test_administer_is_not_politics():
    # "minister" used to substring-match inside "administer".
    assert _cat("Will nurses administer the new vaccine by June?") == "Other"


def test_colleague_is_not_sports():
    # "league" used to substring-match inside "colleague".
    assert _cat("Will he hire a former colleague as CFO?") == "Other"


def test_word_boundary_still_allows_plurals_and_prefixes():
    assert _cat("Will new sanctions be imposed on the regime?") == "Politics"
    assert _cat("Will tariffs on EU goods be doubled?") == "Economy"
    assert _cat("Will Chicago see snowfall before Halloween?") == "Weather"


# --------------------------------------------------------------------------- #
# ~10 representative questions per category
# --------------------------------------------------------------------------- #
CRYPTO = [
    "Will Bitcoin hit $150k by March 2027?",
    "Will BTC close above $120,000 this month?",
    "Ethereum ETF options approved in 2026?",
    "Will Solana flip BNB by market cap?",
    "Will XRP prevail in its next SEC appeal?",
    "New all-time high for dogecoin this year?",
    "Will a memecoin enter the top 10 by market cap?",
    "Will Binance face new US charges in 2026?",
    "Bitcoin up or down on July 15?",
    "Will the largest stablecoin depeg in 2026?",
]

POLITICS = [
    "Will Trump win the 2028 election?",
    "Who will be the next UK prime minister?",
    "Will the government shut down before October?",
    "Will Israel and Hamas reach a ceasefire by Q3?",
    "Will Russia and Ukraine sign a treaty in 2026?",
    "Will the senate confirm the nominee?",
    "Will there be a referendum on EU membership?",
    "Will Iran's supreme leader leave office this year?",
    "Will the president pardon anyone convicted this term?",
    "Will Marine Le Pen win her reelection bid?",
]

ECONOMY = [
    "Will the Fed cut rates in September?",
    "Will the Federal Reserve hold in July?",
    "US recession declared before 2027?",
    "Will CPI come in above 3% for June?",
    "Will the unemployment rate exceed 4.5%?",
    "How many rate cuts in 2026?",
    "Will the ECB cut before year-end?",
    "Will Q2 GDP growth top 2%?",
    "Will the June jobs report beat consensus?",
    "Will the 10-year Treasury yield top 5%?",
]

WEATHER = [
    "Will NYC temperature exceed 100 degrees this July?",
    "Will a Category 5 hurricane make landfall in 2026?",
    "Will it rain in London on the final day?",
    "Will Chicago see snowfall before Halloween?",
    "Will 2026 be the hottest year on record (global temperature)?",
    "Will the storm surge close the port?",
    "Will Phoenix hit 45 celsius this summer?",
    "Will Denver weather delay the launch?",
    "Will it snow in Miami this decade?",
    "Will the temperature in Austin top 105 fahrenheit?",
]

CULTURE = [
    "Will the movie gross $1B worldwide?",
    "Will the album debut at #1 on Billboard?",
    "Who takes Best Picture at the Oscars?",
    "Will the Netflix series get renewed?",
    "Will the song stay #1 on Spotify for 4 weeks?",
    "Will the finale episode break viewership records?",
    "Rotten Tomatoes score above 90 for the sequel?",
    "Will she claim a Grammy in 2027?",
    "Will the box office rebound this summer?",
    "Will the Emmy for best drama go to a streamer?",
]

SPORTS = [
    "Lakers vs Celtics: who takes game 7?",
    "Will Manchester City win the league?",
    "Will the Chiefs make the playoffs?",
    "Will Alcaraz defeat Djokovic at Wimbledon?",
    "Who wins the World Cup 2030?",
    "Will the UFC fight end by knockout?",
    "Will Verstappen win the Monaco Grand Prix?",
    "Will they cover the spread: -3.5?",
    "Will the match go to extra time?",
    "Will he hit 40 home runs by August?",
]


@pytest.mark.parametrize("category,questions", [
    ("Crypto", CRYPTO), ("Politics", POLITICS), ("Economy", ECONOMY),
    ("Weather", WEATHER), ("Culture", CULTURE), ("Sports", SPORTS),
])
def test_representative_questions_classify(category, questions):
    misses = {q: _cat(q) for q in questions if _cat(q) != category}
    assert not misses, f"misclassified for {category}: {misses}"


def test_ordering_politics_beats_sports():
    # Ordered buckets: "Trump vs Harris" is Politics even though Sports owns " vs ".
    assert _cat("Will Trump beat Harris in a 2028 rematch?") == "Politics"


def test_raw_category_fallback_preserved():
    assert _cat("Will the thing happen?", raw_category="esoterica") == "Esoterica"
    assert _cat("Will the thing happen?") == "Other"


# --------------------------------------------------------------------------- #
# Correlated factors (portfolio) — same shared taxonomy
# --------------------------------------------------------------------------- #
def test_factor_global_conflict():
    assert _fac("Will Iran strike Israel again this month?") == "global-conflict"
    # "airstrike" used to ride on the "strike" substring; now an explicit keyword.
    assert _fac("Will an airstrike hit Sanaa this week?") == "global-conflict"


def test_factor_us_rates_and_politics_and_crypto():
    assert _fac("Will the Fed hike rates in July?") == "us-rates"
    assert _fac("Will Trump's approval fall below 40%?") == "us-politics"
    assert _fac("Will Bitcoin close above 100k?") == "crypto-beta"


def test_factor_falls_back_to_category():
    assert _fac("Will the parliament pass the budget?", category="Politics") == "politics"


def test_shared_lists_are_the_single_source():
    # Both consumers must read the one module — the whole point of P2-7.
    assert taxonomy.CATEGORY_KEYWORDS[0][0] == "Crypto"
    assert taxonomy.FACTOR_KEYWORDS[0][0] == "global-conflict"
    assert taxonomy.match_category("Will the Fed cut rates?") == "Economy"
    assert taxonomy.match_factor("Will the Fed cut rates?") == "us-rates"
    assert taxonomy.match_category("Completely unrelated text") is None
