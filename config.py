"""
ApexMind — central configuration.

Plain Python (no YAML dependency). Override any value with an environment
variable of the same name, e.g.  set  APEX_MIN_LIQUIDITY=5000
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent

# Load a local .env (Telegram secrets, overrides) if present. Optional dependency:
# if python-dotenv isn't installed we fall back to the real process environment.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
MEMORY_DIR = ROOT / "memory"
LOGS_DIR = ROOT / "logs"
DATA_DIR = ROOT / "data"
RESEARCH_CACHE_DIR = DATA_DIR / "research_cache"
PROMPTS_FILE = ROOT / "system_prompts.md"

for _d in (MEMORY_DIR, LOGS_DIR, DATA_DIR, RESEARCH_CACHE_DIR):
    _d.mkdir(exist_ok=True)

# Memory files
BELIEFS_FILE = MEMORY_DIR / "beliefs.json"
PREDICTIONS_FILE = MEMORY_DIR / "predictions.json"
CALIBRATION_FILE = MEMORY_DIR / "calibration.json"
LESSONS_FILE = MEMORY_DIR / "lessons.md"

# Generated artifacts
BRIEFING_FILE = DATA_DIR / "briefing_latest.md"
MARKETS_CACHE = DATA_DIR / "markets_latest.json"
REFLECTION_PACKET = DATA_DIR / "reflection_packet.json"
DECISION_MEMO_FILE = DATA_DIR / "decision_memo_latest.md"
BACKTEST_REPORT_MD = DATA_DIR / "backtest_report.md"
BACKTEST_REPORT_JSON = DATA_DIR / "backtest_report.json"


def _env(name: str, default):
    """Read an env override, casting to the type of `default`."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


# --------------------------------------------------------------------------- #
# Polymarket scanning
# --------------------------------------------------------------------------- #
GAMMA_API = _env("APEX_GAMMA_API", "https://gamma-api.polymarket.com")
CLOB_API = _env("APEX_CLOB_API", "https://clob.polymarket.com")

# How many markets to pull and how aggressively to filter them.
# Order by *total* volume (not 24h) so marquee, edge-rich markets (elections, macro,
# major events) enter the pool instead of today's intraday coin-flip churn.
SCAN_LIMIT = _env("APEX_SCAN_LIMIT", 500)          # markets pulled from Gamma (deeper pool = more near-term deal flow)
SCAN_ORDER = _env("APEX_SCAN_ORDER", "volumeNum")  # gamma sort field
SHORTLIST_SIZE = _env("APEX_SHORTLIST_SIZE", 15)   # markets put into the briefing
MIN_LIQUIDITY = _env("APEX_MIN_LIQUIDITY", 2000.0)  # USD, ignore thin markets
MIN_VOLUME = _env("APEX_MIN_VOLUME", 5000.0)        # USD total volume
MIN_DAYS_TO_RESOLUTION = _env("APEX_MIN_DAYS_TO_RES", 1.0)  # skip intraday coin-flips
MAX_DAYS_TO_RESOLUTION = _env("APEX_MAX_DAYS", 400)  # ignore very-long-dated

# --- Edge-aware triage (re-aim attention away from efficient noise) ---------- #
# Higher priority = more attention. Politics/Macro carry ApexMind's real edge
# (resolution-criteria & procedure); short-dated crypto/sports/weather are
# backtested as efficient coin-flips (Brier ~0.24) and get de-prioritised + capped.
CATEGORY_PRIORITY = {
    "Politics": 1.6, "Economy": 1.6, "Other": 1.1, "Culture": 0.9,
    "Sports": 0.6, "Weather": 0.7, "Crypto": 0.6,
}
# Max markets of each category allowed in one shortlist (None = uncapped).
CATEGORY_CAPS = {"Crypto": 2, "Sports": 2, "Weather": 2, "Culture": 2}
# Question substrings that mark intraday / coin-flip markets to exclude outright.
EXCLUDE_PATTERNS = ["up or down", " am et", " pm et", "next hour", "this hour",
                    "by end of day", "in the next "]
# Skip markets already at the extremes (near-mechanical, little edge, e.g. novelty
# longshots like "aliens exist"). The analysable band lives between these.
SHORTLIST_PROB_LO = _env("APEX_PROB_LO", 0.04)
SHORTLIST_PROB_HI = _env("APEX_PROB_HI", 0.96)

# Briefing enrichment — pull recent CLOB price history per shortlisted market.
BRIEFING_INCLUDE_HISTORY = _env("APEX_INCLUDE_HISTORY", True)
HISTORY_DAYS = _env("APEX_HISTORY_DAYS", 7)         # window of price history

# --------------------------------------------------------------------------- #
# Research tools (tools/research.py)
# --------------------------------------------------------------------------- #
# Keyless by default (DuckDuckGo HTML + web fallback). Supply a key/base below for
# higher reliability. Results are cached under data/research_cache/ to stay
# rate-limit friendly.
RESEARCH_CACHE_TTL = _env("APEX_RESEARCH_TTL", 21600)        # seconds (6h)
RESEARCH_MIN_INTERVAL = _env("APEX_RESEARCH_MIN_INTERVAL", 1.0)  # s between live calls
RESEARCH_TIMEOUT = _env("APEX_RESEARCH_TIMEOUT", 20)        # per-request seconds
RESEARCH_USER_AGENT = _env(
    "APEX_RESEARCH_UA",
    "Mozilla/5.0 (compatible; ApexMindResearch/1.0; +https://polymarket.com)")

# Optional web-search backends (auto-selected: Brave > SerpAPI > DuckDuckGo).
BRAVE_API_KEY = _env("BRAVE_API_KEY", "")
SERPAPI_KEY = _env("SERPAPI_KEY", "")
# Optional X/Twitter backend (X API v2; otherwise a degraded site:x.com web fallback).
# The old self-hosted/public Nitter path was removed — most public instances are
# defunct, so it returned nothing while adding broken code to maintain.
X_BEARER_TOKEN = _env("X_BEARER_TOKEN", "")

# Briefing research enrichment — ON by default (set APEX_INCLUDE_RESEARCH=false to
# disable; it adds a few network calls to each scan but front-loads the evidence).
BRIEFING_INCLUDE_RESEARCH = _env("APEX_INCLUDE_RESEARCH", True)
RESEARCH_BRIEFING_MARKETS = _env("APEX_RESEARCH_BRIEFING_MARKETS", 3)  # top-N enriched

# On-chain + polling research backends (all keyless by default; optional keys raise
# rate limits / unlock holder data).
DEXSCREENER_API = _env("APEX_DEXSCREENER_API", "https://api.dexscreener.com")
COINGECKO_API = _env("APEX_COINGECKO_API", "https://api.coingecko.com/api/v3")
BINANCE_FAPI = _env("APEX_BINANCE_FAPI", "https://fapi.binance.com")
COINGECKO_API_KEY = _env("COINGECKO_API_KEY", "")     # optional Coingecko pro key
ETHERSCAN_API_KEY = _env("ETHERSCAN_API_KEY", "")     # optional, for holder counts

# --------------------------------------------------------------------------- #
# Schema sentinel (tools/schema_sentinel.py) — upstream API drift monitor
# --------------------------------------------------------------------------- #
# Pure monitoring: when ON, it validates that Polymarket Gamma/CLOB responses still
# carry the critical fields the pipeline parses, and sends ONE throttled Telegram
# alert per endpoint per interval if a field is renamed/removed. It NEVER raises or
# alters business logic. OFF by default; needs TELEGRAM_* set to actually deliver.
SCHEMA_SENTINEL_ENABLED = _env("APEX_SCHEMA_SENTINEL_ENABLED", False)
SCHEMA_SENTINEL_INTERVAL = _env("APEX_SCHEMA_SENTINEL_INTERVAL", 86400)  # s between alerts/endpoint

# --------------------------------------------------------------------------- #
# Decision policy (used by the Supervisor prompt + scoring)
# --------------------------------------------------------------------------- #
# Minimum edge (|model_prob - market_prob|) before a market is worth a "position".
MIN_EDGE = _env("APEX_MIN_EDGE", 0.08)
# Minimum confidence the Specialist must express to act on an edge.
MIN_CONFIDENCE = _env("APEX_MIN_CONFIDENCE", 0.55)

# Calibration reliability floor: a bucket with fewer than MIN_BUCKET_N resolved
# markets is too small to trust — its realised rate (and `gap`) is noise, not signal.
# scoring.calibration_report flags such buckets `reliable: False` and brackets every
# bucket's realised rate with a Wilson 95% CI so the Reflection role doesn't chase it.
MIN_BUCKET_N = _env("APEX_MIN_BUCKET_N", 8)

# Portfolio risk caps (tools/portfolio.py): flag when one correlated factor carries
# too much conviction or too many positions — a single shock shouldn't sink the book.
PORTFOLIO_MAX_FACTOR_CONVICTION = _env("APEX_MAX_FACTOR_CONVICTION", 8)
PORTFOLIO_MAX_FACTOR_POSITIONS = _env("APEX_MAX_FACTOR_POSITIONS", 3)

# --------------------------------------------------------------------------- #
# Backtest harness (tools/backtest.py)
# --------------------------------------------------------------------------- #
# Read the crowd price this many days *before* resolution (no look-ahead), then
# score a mechanical strategy against the realised outcome.
BACKTEST_LEAD_DAYS = _env("APEX_BACKTEST_LEAD_DAYS", 7)
BACKTEST_WINDOW_DAYS = _env("APEX_BACKTEST_WINDOW_DAYS", 7)   # look-back for momentum/revert
BACKTEST_MAX_MARKETS = _env("APEX_BACKTEST_MAX_MARKETS", 120)
BACKTEST_STRATEGY = _env("APEX_BACKTEST_STRATEGY", "revert")  # market|shrink|steepen|revert|momentum
BACKTEST_FETCH_DELAY = _env("APEX_BACKTEST_FETCH_DELAY", 0.2)  # s between CLOB calls

# --- Category-aware backtest sampling -------------------------------------- #
# ORDERING is the load-bearing fix. The Gamma default sort, "closedTime", is swamped
# by the fast-churning Sports/Weather/Crypto/daily coin-flips that resolve constantly
# (the top-500 closed-by-recency markets contain ~0 Politics/Economy), so a recency
# pull is structurally blind to the niches our edge thesis depends on. "volumeNum"
# surfaces the marquee, high-volume resolutions instead — the same markets the live
# scanner triages (SCAN_ORDER) and the ones we'd actually take positions on — which
# flips the 90-day thesis share from ~5% to ~85%. Keep this on volumeNum.
BACKTEST_ORDER = _env("APEX_BACKTEST_ORDER", "volumeNum")        # volumeNum | closedTime
# Stratification is a SAFETY NET on top of the ordering: GUARANTEE at least
# BACKTEST_CATEGORY_MIN markets per named category, and CAP the efficient coin-flip
# categories so a burst of them can never crowd out the thesis. To restore the old
# flat-recency behaviour set APEX_BACKTEST_ORDER=closedTime, APEX_BACKTEST_CATEGORIES=""
# and APEX_BACKTEST_CATEGORY_CAPS="".
BACKTEST_CATEGORIES = [c.strip() for c in
                       _env("APEX_BACKTEST_CATEGORIES", "Politics,Economy,Other").split(",")
                       if c.strip()]
BACKTEST_CATEGORY_MIN = _env("APEX_BACKTEST_CATEGORY_MIN", 6)   # floor per named category
BACKTEST_POOL_PAGES = _env("APEX_BACKTEST_POOL_PAGES", 1)       # Gamma ignores offset here; 1 page


def _parse_category_caps(raw: str) -> dict:
    """Parse 'Crypto:8,Sports:12,Weather:10' -> {'Crypto':8,'Sports':12,'Weather':10}."""
    caps: dict[str, int] = {}
    for part in (raw or "").split(","):
        name, sep, val = part.strip().partition(":")
        if sep and name.strip():
            try:
                caps[name.strip()] = int(val)
            except ValueError:
                continue
    return caps


# Per-category ceilings on the efficient, high-churn categories (backtested as ~coin
# flips, Brier ~0.24) so they cannot crowd out the thesis categories in the sample.
BACKTEST_CATEGORY_CAPS = _parse_category_caps(
    _env("APEX_BACKTEST_CATEGORY_CAPS", "Crypto:15,Sports:20,Weather:15"))

# --------------------------------------------------------------------------- #
# Telegram notifications
# --------------------------------------------------------------------------- #
# Loaded from .env (see .env.example). If the token/chat id are blank, ApexMind
# simply skips sending — it never crashes for a missing notifier.
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", "")
NOTIFY_ENABLED = _env("APEX_NOTIFY_ENABLED", True)
TELEGRAM_API = _env("APEX_TELEGRAM_API", "https://api.telegram.org")


def telegram_ready() -> bool:
    """True only when notifications are enabled AND both secrets are present."""
    return bool(NOTIFY_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


# --------------------------------------------------------------------------- #
# Optional headless automation (scheduled runs)
# --------------------------------------------------------------------------- #
# Path to the Claude Code CLI. Only used by run_analysis.py --auto.
CLAUDE_CLI = _env("APEX_CLAUDE_CLI", "claude")
