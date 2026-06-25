"""
tools/research.py — ApexMind research instruments.

Keyless-by-default web + X search and page reading for the Python "hands" layer.
Every network call is **best-effort**: on any failure it returns an empty, structured
result instead of raising, so an analysis run never dies because a source was down.

Two research pathways exist — use the right one:
  * **Inside a live Claude Code session**, prefer Claude Code's native WebSearch /
    WebFetch tools — they are the most reliable and need no scraping.
  * **This module** powers headless (`run_analysis.py --auto`) runs, the
    `python main_agent.py research "<query>"` CLI, and optional briefing enrichment,
    where no in-session tools are available.

Backend selection (best available first; no keys required):
  web_search  : Brave API (BRAVE_API_KEY) → SerpAPI (SERPAPI_KEY) → DuckDuckGo HTML
  x_search    : X API v2 (X_BEARER_TOKEN) → Nitter (APEX_NITTER_BASE) → web fallback
  browse_page : direct fetch + readable-text extraction

Results are cached as JSON under data/research_cache/ with a TTL (config) to avoid
redundant calls and to stay rate-limit friendly. A module-level throttle enforces a
minimum interval between live HTTP calls.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import requests

import config

# BeautifulSoup gives clean HTML parsing; we degrade to regex if it's not installed.
try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:  # pragma: no cover - optional dependency
    _HAS_BS4 = False


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def _clean(text: Any) -> str:
    """Unescape HTML entities and collapse whitespace."""
    return re.sub(r"\s+", " ", html.unescape(str(text or ""))).strip()


# --------------------------------------------------------------------------- #
# Caching (one JSON file per query/url, with TTL)
# --------------------------------------------------------------------------- #
def _cache_path(kind: str, key: str):
    digest = hashlib.sha1(f"{kind}:{key}".encode("utf-8")).hexdigest()[:16]
    return config.RESEARCH_CACHE_DIR / f"{kind}-{digest}.json"


def _cache_get(kind: str, key: str, ttl: int | None = None) -> Any:
    ttl = config.RESEARCH_CACHE_TTL if ttl is None else ttl
    path = _cache_path(kind, key)
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if ttl and (time.time() - obj.get("ts", 0)) > ttl:
        return None
    return obj.get("data")


def _cache_put(kind: str, key: str, data: Any) -> None:
    config.RESEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"kind": kind, "key": key, "ts": time.time(), "data": data}
    try:
        _cache_path(kind, key).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def clear_cache() -> int:
    """Delete all cached research. Returns the number of files removed."""
    n = 0
    for f in config.RESEARCH_CACHE_DIR.glob("*.json"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


# --------------------------------------------------------------------------- #
# Throttled HTTP
# --------------------------------------------------------------------------- #
_last_call = [0.0]


def _throttle() -> None:
    wait = config.RESEARCH_MIN_INTERVAL - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def _headers(extra: dict | None = None) -> dict:
    h = {"User-Agent": config.RESEARCH_USER_AGENT,
         "Accept-Language": "en-US,en;q=0.9"}
    if extra:
        h.update(extra)
    return h


def _http_get(url: str, params: dict | None = None,
              headers: dict | None = None, timeout: int | None = None):
    _throttle()
    return requests.get(url, params=params, headers=_headers(headers),
                        timeout=timeout or config.RESEARCH_TIMEOUT)


def _http_post(url: str, data: dict | None = None,
               headers: dict | None = None, timeout: int | None = None):
    _throttle()
    return requests.post(url, data=data, headers=_headers(headers),
                         timeout=timeout or config.RESEARCH_TIMEOUT)


# --------------------------------------------------------------------------- #
# WEB SEARCH
# --------------------------------------------------------------------------- #
def web_search(query: str, num_results: int = 10,
               use_cache: bool = True) -> list[dict[str, str]]:
    """Search the web. Returns [{title, url, snippet, source}], newest backend first.

    Never raises — returns [] if every backend fails or no network is available.
    """
    query = (query or "").strip()
    if not query:
        return []
    ck = f"{query}|{num_results}"
    if use_cache:
        hit = _cache_get("websearch", ck)
        if hit is not None:
            return hit

    results: list[dict[str, str]] = []
    try:
        if config.BRAVE_API_KEY:
            results = _brave_search(query, num_results)
        elif config.SERPAPI_KEY:
            results = _serpapi_search(query, num_results)
        else:
            results = _ddg_search(query, num_results)
    except requests.RequestException:
        results = []

    results = results[:num_results]
    if use_cache and results:
        _cache_put("websearch", ck, results)
    return results


def _brave_search(query: str, n: int) -> list[dict[str, str]]:
    resp = _http_get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": min(max(n, 1), 20)},
        headers={"X-Subscription-Token": config.BRAVE_API_KEY,
                 "Accept": "application/json"})
    if resp.status_code != 200:
        return []
    data = resp.json()
    out = []
    for r in (data.get("web") or {}).get("results", []):
        out.append({"title": _clean(r.get("title")), "url": r.get("url", ""),
                    "snippet": _clean(r.get("description")), "source": "brave"})
    return out


def _serpapi_search(query: str, n: int) -> list[dict[str, str]]:
    resp = _http_get("https://serpapi.com/search.json",
                     params={"q": query, "num": n, "engine": "google",
                             "api_key": config.SERPAPI_KEY})
    if resp.status_code != 200:
        return []
    data = resp.json()
    out = []
    for r in data.get("organic_results", []):
        out.append({"title": _clean(r.get("title")), "url": r.get("link", ""),
                    "snippet": _clean(r.get("snippet")), "source": "serpapi"})
    return out


def _ddg_unwrap(href: str) -> str:
    """DuckDuckGo HTML wraps links as //duckduckgo.com/l/?uddg=<encoded>."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs:
            return qs["uddg"][0]
    return href


def _ddg_search(query: str, n: int) -> list[dict[str, str]]:
    # DuckDuckGo's HTML endpoint returns a 202 challenge on GET; POST works.
    resp = _http_post("https://html.duckduckgo.com/html/", data={"q": query})
    if resp.status_code != 200:
        return []
    return _parse_ddg_html(resp.text, n)


def _parse_ddg_html(text: str, n: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if _HAS_BS4:
        soup = BeautifulSoup(text, "html.parser")
        for res in soup.select(".result, .web-result"):
            a = res.select_one("a.result__a")
            if not a:
                continue
            url = _ddg_unwrap(a.get("href", ""))
            sn = res.select_one(".result__snippet")
            out.append({"title": _clean(a.get_text(" ", strip=True)), "url": url,
                        "snippet": _clean(sn.get_text(" ", strip=True)) if sn else "",
                        "source": "duckduckgo"})
            if len(out) >= n:
                break
    else:
        for m in re.finditer(
                r'class="result__a"[^>]*href="(?P<u>.*?)".*?>(?P<t>.*?)</a>',
                text, re.S):
            url = _ddg_unwrap(html.unescape(m.group("u")))
            out.append({"title": _clean(re.sub("<.*?>", "", m.group("t"))),
                        "url": url, "snippet": "", "source": "duckduckgo"})
            if len(out) >= n:
                break
    return out


# --------------------------------------------------------------------------- #
# X / TWITTER SEARCH
# --------------------------------------------------------------------------- #
def x_search(query: str, limit: int = 5, mode: str = "Latest",
             use_cache: bool = True) -> list[dict[str, str]]:
    """Search X/Twitter. Returns [{text, author, url, created_at, source}].

    mode "Latest" sorts by recency; anything else sorts by relevance (where the
    backend supports it). Keyless mode falls back to a site:x.com web search, which
    is degraded but never fails the run.
    """
    query = (query or "").strip()
    if not query:
        return []
    ck = f"{query}|{limit}|{mode}"
    if use_cache:
        hit = _cache_get("xsearch", ck)
        if hit is not None:
            return hit

    out: list[dict[str, str]] = []
    try:
        if config.X_BEARER_TOKEN:
            out = _x_api_search(query, limit, mode)
        elif config.NITTER_BASE:
            out = _nitter_search(query, limit, mode)
        else:
            out = _x_web_fallback(query, limit)
    except requests.RequestException:
        out = []

    out = out[:limit]
    if use_cache and out:
        _cache_put("xsearch", ck, out)
    return out


def _x_api_search(query: str, limit: int, mode: str) -> list[dict[str, str]]:
    resp = _http_get(
        "https://api.twitter.com/2/tweets/search/recent",
        params={"query": query,
                "max_results": min(max(limit, 10), 100),  # API floor is 10
                "tweet.fields": "created_at,author_id,public_metrics",
                "sort_order": "recency" if mode.lower() == "latest" else "relevancy"},
        headers={"Authorization": f"Bearer {config.X_BEARER_TOKEN}"})
    if resp.status_code != 200:
        return []
    data = resp.json()
    out = []
    for t in data.get("data", []):
        out.append({"text": _clean(t.get("text")), "author": str(t.get("author_id", "")),
                    "url": f"https://x.com/i/web/status/{t.get('id', '')}",
                    "created_at": t.get("created_at", ""), "source": "x-api"})
    return out


def _nitter_search(query: str, limit: int, mode: str) -> list[dict[str, str]]:
    base = config.NITTER_BASE.rstrip("/")
    resp = _http_get(f"{base}/search",
                     params={"q": query,
                             "f": "tweets" if mode.lower() == "latest" else ""})
    if resp.status_code != 200 or not _HAS_BS4:
        return []
    out = []
    soup = BeautifulSoup(resp.text, "html.parser")
    for item in soup.select(".timeline-item"):
        content = item.select_one(".tweet-content")
        if not content:
            continue
        user = item.select_one(".username")
        link = item.select_one("a.tweet-link")
        href = link.get("href", "") if link else ""
        url = (base + href) if href.startswith("/") else href
        out.append({"text": _clean(content.get_text(" ", strip=True)),
                    "author": _clean(user.get_text(strip=True)) if user else "",
                    "url": url, "created_at": "", "source": "nitter"})
        if len(out) >= limit:
            break
    return out


def _x_web_fallback(query: str, limit: int) -> list[dict[str, str]]:
    hits = web_search(f"{query} (site:x.com OR site:twitter.com)", num_results=limit)
    return [{"text": _clean(h.get("snippet") or h.get("title")), "author": "",
             "url": h.get("url", ""), "created_at": "", "source": "web-fallback"}
            for h in hits][:limit]


# --------------------------------------------------------------------------- #
# PAGE READING
# --------------------------------------------------------------------------- #
def browse_page(url: str, max_chars: int = 6000,
                use_cache: bool = True) -> dict[str, Any]:
    """Fetch a URL and extract readable text.

    Returns {url, title, text, ok, source[, error]}. ok=False on any failure.
    """
    url = (url or "").strip()
    blank = {"url": url, "title": "", "text": "", "ok": False, "source": "browse"}
    if not url:
        return blank
    if use_cache:
        hit = _cache_get("page", url)
        if hit is not None:
            return hit

    result = dict(blank)
    try:
        resp = _http_get(url, timeout=config.RESEARCH_TIMEOUT + 5)
        ctype = resp.headers.get("Content-Type", "")
        if resp.status_code == 200 and "html" in ctype.lower():
            title, text = _extract_readable(resp.text)
            result = {"url": url, "title": title, "text": text[:max_chars],
                      "ok": True, "source": "browse"}
        elif resp.status_code == 200:                      # txt / json / etc.
            result = {"url": url, "title": "", "text": _clean(resp.text)[:max_chars],
                      "ok": True, "source": "browse"}
        else:
            result["error"] = f"HTTP {resp.status_code}"
    except requests.RequestException as exc:
        result["error"] = str(exc)

    if use_cache and result.get("ok"):
        _cache_put("page", url, result)
    return result


def _extract_readable(text: str) -> tuple[str, str]:
    if _HAS_BS4:
        soup = BeautifulSoup(text, "html.parser")
        title = _clean(soup.title.get_text()) if soup.title else ""
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "aside", "noscript", "form", "svg"]):
            tag.decompose()
        main = soup.find("article") or soup.find("main") or soup.body or soup
        return title, _clean(main.get_text(" ", strip=True))
    # regex fallback
    tm = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    title = _clean(re.sub("<.*?>", "", tm.group(1))) if tm else ""
    body = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    body = re.sub(r"(?s)<.*?>", " ", body)
    return title, _clean(body)


# --------------------------------------------------------------------------- #
# Bundles (used by the CLI and briefing enrichment)
# --------------------------------------------------------------------------- #
def gather(query: str, web_n: int = 4, x_n: int = 3) -> dict[str, Any]:
    """Convenience: run a web + X search for one topic and bundle the results."""
    return {
        "query": query,
        "web": web_search(query, web_n),
        "x": x_search(query, x_n),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------- #
# Evidence ledger helpers — turn research into recordable, scored evidence
# --------------------------------------------------------------------------- #
def evidence_entry(query: str, source_url: str, key_finding: str,
                   likelihood_ratio: float | None = None,
                   source_tier: str = "primary",
                   direction: str | None = None) -> dict[str, Any]:
    """Build one normalized evidence-ledger entry from a piece of research.

    The Specialist supplies the judgement (`key_finding`, the multiplicative
    `likelihood_ratio`, and which side it supports); this just stamps and shapes it
    so it drops straight into a prediction's `evidence` array (see memory_store).

      likelihood_ratio: >1 supports YES, <1 supports NO, 1.0/None = context only.
      source_tier:      primary | expert | market | social
    """
    return {
        "query": query,
        "source_url": source_url,
        "source_tier": source_tier,
        "key_finding": key_finding,
        "likelihood_ratio": likelihood_ratio,
        "direction": direction,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def evidence_from_hit(hit: dict[str, Any], key_finding: str,
                      likelihood_ratio: float | None = None,
                      source_tier: str = "expert",
                      direction: str | None = None) -> dict[str, Any]:
    """Build an evidence entry from a web_search/x_search result dict."""
    return evidence_entry(
        query=hit.get("query", ""),
        source_url=hit.get("url", ""),
        key_finding=key_finding or hit.get("snippet", "")[:200],
        likelihood_ratio=likelihood_ratio,
        source_tier=source_tier,
        direction=direction,
    )


def evidence_from_onchain(data: dict[str, Any], key_finding: str,
                          likelihood_ratio: float | None = None,
                          direction: str | None = None) -> dict[str, Any]:
    """Build a `market`-tier evidence entry from a crypto_onchain() result."""
    url = (data.get("sources") or [""])[0]
    return evidence_entry(query=data.get("query", ""), source_url=url,
                          key_finding=key_finding, likelihood_ratio=likelihood_ratio,
                          source_tier="market", direction=direction)


def evidence_from_polling(data: dict[str, Any], key_finding: str,
                          likelihood_ratio: float | None = None,
                          direction: str | None = None) -> dict[str, Any]:
    """Build an `expert`-tier evidence entry from a polling_search() result."""
    src = data.get("top_source") or (data.get("hits") or [{}])[0]
    url = src.get("url", "") if isinstance(src, dict) else ""
    return evidence_entry(query=data.get("topic", ""), source_url=url,
                          key_finding=key_finding, likelihood_ratio=likelihood_ratio,
                          source_tier="expert", direction=direction)


# --------------------------------------------------------------------------- #
# ON-CHAIN crypto research (DexScreener + Coingecko + Binance funding; keyless)
# --------------------------------------------------------------------------- #
def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coingecko(query: str) -> dict[str, Any] | None:
    headers = {"x-cg-demo-api-key": config.COINGECKO_API_KEY} \
        if config.COINGECKO_API_KEY else None
    s = _http_get(f"{config.COINGECKO_API}/search", params={"query": query},
                  headers=headers)
    if s.status_code != 200:
        return None
    coins = s.json().get("coins", [])
    if not coins:
        return None
    cid = coins[0].get("id")
    m = _http_get(f"{config.COINGECKO_API}/coins/markets",
                  params={"vs_currency": "usd", "ids": cid}, headers=headers)
    if m.status_code != 200 or not m.json():
        return None
    d = m.json()[0]
    return {
        "id": cid, "symbol": str(d.get("symbol", "")).upper(), "name": d.get("name"),
        "price_usd": d.get("current_price"), "market_cap": d.get("market_cap"),
        "rank": d.get("market_cap_rank"), "volume_24h": d.get("total_volume"),
        "change_24h": d.get("price_change_percentage_24h"),
        "circulating_supply": d.get("circulating_supply"),
        "url": f"https://www.coingecko.com/en/coins/{cid}",
    }


def _dexscreener(query: str) -> dict[str, Any] | None:
    r = _http_get(f"{config.DEXSCREENER_API}/latest/dex/search", params={"q": query})
    if r.status_code != 200:
        return None
    pairs = r.json().get("pairs") or []
    if not pairs:
        return None
    # Top pair by 24h volume = the most-traded venue (best activity/whale signal).
    p = max(pairs, key=lambda x: (x.get("volume") or {}).get("h24") or 0)
    txns = (p.get("txns") or {}).get("h24") or {}
    base = (p.get("baseToken") or {}).get("symbol", "?")
    quote = (p.get("quoteToken") or {}).get("symbol", "?")
    return {
        "pair": f"{base}/{quote}", "chain": p.get("chainId"), "dex": p.get("dexId"),
        "price_usd": _num(p.get("priceUsd")),
        "liquidity_usd": (p.get("liquidity") or {}).get("usd"),
        "volume_24h": (p.get("volume") or {}).get("h24"),
        "change_24h": (p.get("priceChange") or {}).get("h24"),
        "buys_24h": txns.get("buys"), "sells_24h": txns.get("sells"),
        "url": p.get("url"),
    }


def _binance_funding(symbol: str) -> dict[str, Any] | None:
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"
    r = _http_get(f"{config.BINANCE_FAPI}/fapi/v1/premiumIndex", params={"symbol": sym})
    if r.status_code != 200:
        return None
    d = r.json()
    if not isinstance(d, dict) or "lastFundingRate" not in d:
        return None
    return {"symbol": sym, "last_funding_rate": _num(d.get("lastFundingRate")),
            "mark_price": _num(d.get("markPrice")),
            "url": f"https://www.binance.com/en/futures/{sym}"}


def crypto_onchain(token_or_query: str, use_cache: bool = True) -> dict[str, Any]:
    """Pull keyless on-chain / market metrics for a token.

    Combines **Coingecko** (price, market cap, rank, supply, 24h vol/change),
    **DexScreener** (top-pair DEX liquidity, 24h volume, and buy/sell tx counts — a
    whale/activity proxy), and **Binance** perp **funding rate** (positioning).
    Best-effort: any source that fails is simply None. Returns a dict with a
    `sources` URL list for the evidence ledger.
    """
    q = (token_or_query or "").strip()
    if not q:
        return {}
    if use_cache:
        hit = _cache_get("onchain", q)
        if hit is not None:
            return hit

    result: dict[str, Any] = {"query": q, "coingecko": None, "dex": None,
                              "funding": None, "sources": [], "fetched_at": _now_iso()}
    try:
        result["coingecko"] = _coingecko(q)
    except requests.RequestException:
        pass
    try:
        result["dex"] = _dexscreener(q)
    except requests.RequestException:
        pass
    symbol = (result["coingecko"] or {}).get("symbol") or q
    try:
        result["funding"] = _binance_funding(symbol)
    except requests.RequestException:
        pass

    for sec in ("coingecko", "dex", "funding"):
        if result[sec] and result[sec].get("url"):
            result["sources"].append(result[sec]["url"])

    if use_cache and (result["coingecko"] or result["dex"] or result["funding"]):
        _cache_put("onchain", q, result)
    return result


def format_onchain_md(d: dict[str, Any]) -> str:
    if not d or not (d.get("coingecko") or d.get("dex") or d.get("funding")):
        return "_no on-chain data found_"
    L = [f"**On-chain — {d.get('query')}**"]
    cg = d.get("coingecko")
    if cg:
        L.append(f"- CoinGecko: {cg['symbol']} ${cg.get('price_usd')} · "
                 f"mcap ${cg.get('market_cap'):,} (rank {cg.get('rank')}) · "
                 f"24h vol ${cg.get('volume_24h'):,} · 24h {cg.get('change_24h'):+.1f}%"
                 if cg.get("market_cap") else f"- CoinGecko: {cg['symbol']} "
                 f"${cg.get('price_usd')}")
    dx = d.get("dex")
    if dx:
        L.append(f"- DEX ({dx.get('dex')}/{dx.get('chain')}) {dx.get('pair')}: "
                 f"liq ${dx.get('liquidity_usd'):,} · 24h vol ${dx.get('volume_24h'):,} · "
                 f"txns {dx.get('buys_24h')}B/{dx.get('sells_24h')}S · "
                 f"24h {dx.get('change_24h')}%"
                 if dx.get("liquidity_usd") else f"- DEX {dx.get('pair')}")
    fr = d.get("funding")
    if fr:
        rate = fr.get("last_funding_rate")
        L.append(f"- Funding ({fr['symbol']}): "
                 f"{rate*100:+.4f}%/8h" if rate is not None else
                 f"- Funding ({fr['symbol']}): n/a")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# POLLING research (aggregator-biased web search + read)
# --------------------------------------------------------------------------- #
_AGGREGATOR_DOMAINS = ["realclearpolitics", "fivethirtyeight", "natesilver",
                       "silverbulletin", "wikipedia.org", "270towin", "racetothewh",
                       "electionbettingodds", "economist.com", "ipsos", "yougov"]


def polling_search(topic: str, browse_top: bool = True,
                   use_cache: bool = True) -> dict[str, Any]:
    """Find and surface the latest polling aggregates for a topic.

    Web-searches with an aggregate-biased query, ranks known aggregator domains
    (RealClearPolitics / FiveThirtyEight / Silver Bulletin / Wikipedia / pollsters)
    first, and (optionally) reads the top aggregator page so the Specialist can
    extract the current average. Returns hits + an optional read excerpt.
    """
    topic = (topic or "").strip()
    if not topic:
        return {}
    if use_cache:
        hit = _cache_get("polling", topic)
        if hit is not None:
            return hit

    hits = web_search(f"{topic} polling average aggregate", num_results=8)

    def is_agg(h):
        u = (h.get("url") or "").lower()
        return any(dom in u for dom in _AGGREGATOR_DOMAINS)

    hits.sort(key=is_agg, reverse=True)
    top = None
    if browse_top and hits:
        page = browse_page(hits[0]["url"])
        if page.get("ok") and page.get("text"):
            top = {"url": hits[0]["url"], "title": page.get("title"),
                   "excerpt": page["text"][:800]}

    result = {
        "topic": topic,
        "hits": [{**h, "aggregator": is_agg(h)} for h in hits[:6]],
        "top_source": top,
        "fetched_at": _now_iso(),
    }
    if use_cache and hits:
        _cache_put("polling", topic, result)
    return result


def format_polling_md(d: dict[str, Any]) -> str:
    if not d or not d.get("hits"):
        return "_no polling results found_"
    L = [f"**Polling — {d.get('topic')}**"]
    for h in d["hits"]:
        tag = "📊 " if h.get("aggregator") else "  "
        sn = f" — {h['snippet'][:140]}" if h.get("snippet") else ""
        L.append(f"{tag}[{h.get('title') or h.get('url')}]({h.get('url')}){sn}")
    if d.get("top_source"):
        L.append(f"\n_read {d['top_source']['url']}:_ "
                 f"{d['top_source']['excerpt'][:300]}…")
    return "\n".join(L)


def format_bundle_md(bundle: dict[str, Any], web_n: int = 3, x_n: int = 3) -> str:
    """Render a gather() bundle as compact Markdown for briefings / the CLI."""
    lines: list[str] = []
    web = (bundle.get("web") or [])[:web_n]
    if web:
        lines.append("**Web:**")
        for r in web:
            sn = f" — {r['snippet'][:200]}" if r.get("snippet") else ""
            lines.append(f"  - [{r.get('title') or r.get('url')}]({r.get('url')}){sn}")
    xs = (bundle.get("x") or [])[:x_n]
    if xs:
        lines.append("**X:**")
        for t in xs:
            who = f"@{t['author']} " if t.get("author") else ""
            lines.append(f"  - {who}{t.get('text', '')[:200]}  {t.get('url', '')}")
    if not web and not xs:
        lines.append("_no research results (source unavailable or rate-limited)_")
    return "\n".join(lines)


def attach_to_markets(markets: list[dict[str, Any]],
                      top_n: int | None = None) -> list[dict[str, Any]]:
    """Best-effort: attach a research `gather()` bundle to the top-N markets in-place."""
    top_n = config.RESEARCH_BRIEFING_MARKETS if top_n is None else top_n
    for m in markets[:top_n]:
        try:
            m["research"] = gather(m.get("question", ""), web_n=3, x_n=2)
        except Exception:  # noqa: BLE001 — enrichment must never be fatal
            m["research"] = None
    return markets


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Polymarket prediction markets"
    print(f"# Research: {q}\n")
    print(format_bundle_md(gather(q)))
