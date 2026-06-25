#!/usr/bin/env python3
"""
ApexMind — main orchestrator CLI.

The Python layer is deterministic plumbing; the *reasoning* is done by Claude Code
(you, in a session) reading system_prompts.md + the briefing this script produces.

Typical loop
------------
  python main_agent.py scan        # pull markets (+price history), build briefing
                                    # -> then reason in Claude Code (Supervisor/Specialist)
  python main_agent.py research "<query>"   # manual web + X deep-dive (cached)
  python main_agent.py record '<json>'   # log each prediction the reasoning produced
  python main_agent.py notify --file memo.json   # push the Decision Memo to Telegram
  python main_agent.py status      # show track record & calibration
  python main_agent.py auto-resolve              # settle predictions from Polymarket
  python main_agent.py resolve <pred_id> <0|1>   # mark a market resolved manually
  python main_agent.py reflect     # build a reflection packet for the Reflection role
  python main_agent.py backtest --days 60        # bootstrap calibration on resolved markets
  python main_agent.py notify-test               # verify Telegram wiring

Run `python main_agent.py --help` for the full list.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
from tools import (backtest, briefing, logger, memory_store, notification,
                  polymarket, portfolio, research, schema_sentinel, scoring)

# Windows consoles default to cp1252 and choke on the emojis / sparklines we print.
# Force UTF-8 output so the CLI is safe everywhere.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_scan(_args) -> None:
    print("Scanning Polymarket (Gamma API)…")
    everything = polymarket.scan_markets()
    short = polymarket.shortlist(everything)
    if config.BRIEFING_INCLUDE_HISTORY:
        print(f"  enriching {len(short)} markets with {config.HISTORY_DAYS}d price history…")
        polymarket.attach_price_history(short)
    if config.BRIEFING_INCLUDE_RESEARCH:
        print(f"  researching top {config.RESEARCH_BRIEFING_MARKETS} markets…")
        research.attach_to_markets(short)
    briefing.write_briefing(short, total_scanned=len(everything))
    logger.log_event("scan", {"scanned": len(everything),
                              "shortlisted": len(short)})
    print(f"  scanned {len(everything)} binary markets")
    print(f"  shortlisted {len(short)}")
    print(f"  briefing written -> {config.BRIEFING_FILE}")
    print("\nNext: in Claude Code, run the SUPERVISOR role over the briefing,")
    print("then log each call with:  python main_agent.py record '<json>'")


def _load_json_input(file_path: str | None, inline: str | None,
                     allow_empty: bool = False) -> dict:
    """Load a JSON object from --file, an inline arg, or stdin ('-').

    utf-8-sig tolerates the BOM that Notepad / PowerShell `Set-Content` add.
    With allow_empty=True, a missing payload yields {} instead of blocking on stdin.
    """
    if file_path:
        raw = Path(file_path).read_text(encoding="utf-8-sig")
    elif inline in (None, "-"):
        if allow_empty and inline is None:
            return {}
        raw = sys.stdin.read()
    else:
        raw = inline
    if allow_empty and not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"Invalid JSON: {e}")


def cmd_record(args) -> None:
    """Record a prediction the reasoning layer produced.

    Accepts the JSON three ways (most-robust first on Windows):
      --file pred.json   read JSON from a file (shell-quoting proof)
      record '<json>'    JSON as a single argv (works great in bash/Git Bash)
      record -           read JSON from stdin
    """
    entry = _load_json_input(args.file, args.json)

    # Backfill market fields from the cached shortlist if only an id was given.
    if entry.get("market_id"):
        cache = json.loads(config.MARKETS_CACHE.read_text(encoding="utf-8")) \
            if config.MARKETS_CACHE.exists() else {"shortlist": []}
        for m in cache.get("shortlist", []):
            if m["id"] == str(entry["market_id"]):
                entry.setdefault("question", m["question"])
                if entry.get("market_prob") is None:
                    entry["market_prob"] = m["market_prob"]
                entry.setdefault("end_date", m["end_date"])
                entry.setdefault("url", m["url"])
                entry.setdefault("liquidity", m.get("liquidity"))
                entry.setdefault("category", m.get("category"))
                break

    stored = memory_store.record_prediction(entry)
    logger.log_event("prediction", stored)
    print(f"Recorded {stored['pred_id']}: {stored.get('decision', '?')} "
          f"{stored.get('direction', '')}  model={stored.get('model_prob')} "
          f"market={stored.get('market_prob')} edge={stored.get('edge')}")

    # Soft confidence advisory: flag over-confidence on thin / social-sourced calls.
    conf = stored.get("confidence")
    if conf is not None:
        ceiling = memory_store.suggest_confidence_ceiling(
            stored.get("liquidity"), stored.get("evidence"))
        if conf > ceiling:
            print(f"  ⚠ confidence {conf} exceeds suggested ceiling {ceiling} "
                  "(thin liquidity and/or social-tier evidence) — consider lowering.")
    if stored.get("decision") == "POSITION" and not stored.get("evidence"):
        print("  ⚠ POSITION recorded with an EMPTY evidence ledger — that's a hunch, "
              "not a position. Add evidence or downgrade to PASS.")


def cmd_resolve(args) -> None:
    stored = memory_store.resolve_prediction(args.pred_id, args.outcome)
    if stored is None:
        sys.exit(f"No prediction with id {args.pred_id}")
    memory_store.recompute_calibration()
    logger.log_event("resolution", {"pred_id": args.pred_id,
                                    "outcome": args.outcome,
                                    "brier": stored.get("brier")})
    print(f"Resolved {args.pred_id}: outcome={args.outcome} "
          f"brier={stored.get('brier')}")


def cmd_status(_args) -> None:
    from tabulate import tabulate

    preds = memory_store.load_predictions()
    resolved = [p for p in preds if p.get("status") == "resolved"]
    openp = [p for p in preds if p.get("status") == "open"]
    calib = memory_store.recompute_calibration()

    print(f"\nApexMind track record — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print(f"  total predictions : {len(preds)}")
    print(f"  open / resolved   : {len(openp)} / {len(resolved)}")
    if calib.get("n"):
        edge = calib.get("edge_vs_market")
        if edge is None:
            print(f"  mean Brier        : {calib['mean_brier']}  (market baseline n/a)")
        else:
            verdict = "model beat crowd" if edge > 0 else "crowd won"
            print(f"  mean Brier        : {calib['mean_brier']}  "
                  f"(market {calib.get('market_baseline_brier')}, "
                  f"edge {edge:+} → {verdict})")
        hr = scoring.hit_rate(resolved)
        print(f"  position hit-rate : {hr['hit_rate']} "
              f"over {hr['n_positions']} positions")

        # --- Live reasoning baseline: the backtest, but on our OWN resolved calls. ---
        # Per-category — which niches actually pay (real edge vs noise)?
        cats = scoring.category_report(resolved)
        if cats:
            rows = [[c, v["n"], v["mean_brier"], v["edge_vs_market"],
                     v["position_hit_rate"], v["n_positions"]]
                    for c, v in sorted(cats.items(), key=lambda kv: -kv[1]["n"])]
            print("\nReasoning baseline by category (model vs crowd, resolved calls):")
            print(tabulate(rows, headers=["category", "n", "Brier", "edge",
                                          "hit", "pos"]))
        # Per dominant evidence tier — do primary/expert calls resolve better?
        et = scoring.evidence_tier_report(resolved)
        tiers = et.get("by_dominant_tier") or {}
        if tiers:
            rows = [[t, v["n"], v["mean_brier"]]
                    for t, v in sorted(tiers.items(), key=lambda kv: -kv[1]["n"])]
            print(f"\nBy dominant evidence tier "
                  f"(low-tier share {et.get('low_tier_share')}):")
            print(tabulate(rows, headers=["tier", "n", "Brier"]))
    elif resolved:
        print("  (resolved calls present but missing model_prob/outcome — cannot score)")
    else:
        print("  (no resolved predictions yet — the reasoning baseline activates once "
              "calls settle; run `auto-resolve` as deadlines pass)")

    if openp:
        rows = [[p["pred_id"], p.get("question", "")[:45],
                 p.get("model_prob"), p.get("market_prob"),
                 p.get("decision"), p.get("direction"), p.get("conviction")]
                for p in openp]
        print("\nOpen positions:")
        print(tabulate(rows, headers=["id", "question", "model", "mkt",
                                      "decision", "dir", "conv"]))


def cmd_reflect(_args) -> None:
    """Assemble a reflection packet of recently-resolved predictions."""
    preds = memory_store.load_predictions()
    resolved = [p for p in preds if p.get("status") == "resolved"]
    calib = memory_store.recompute_calibration()
    evidence_analytics = scoring.evidence_tier_report(resolved)
    category_performance = scoring.category_report(resolved)

    packet = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "calibration": calib,
        "evidence_analytics": evidence_analytics,
        "category_performance": category_performance,
        "resolved_predictions": resolved,
        "instructions": "Adopt the REFLECTION role in system_prompts.md. "
                        "Audit evidence_analytics (does a higher source-tier mean "
                        "lower Brier? is low_tier_share creeping up?) and "
                        "category_performance (where is live edge real vs noise?). "
                        "Score each as Hit/Miss/Lucky/Unlucky, diagnose root "
                        "causes, then update memory/lessons.md and "
                        "memory/beliefs.json. Append lessons with: "
                        "python main_agent.py lesson '<text>' --category <Cat>",
    }
    out = config.REFLECTION_PACKET
    out.write_text(json.dumps(packet, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    logger.log_event("reflect", {"n_resolved": len(resolved)})
    print(f"Reflection packet ({len(resolved)} resolved) -> {out}")
    et = evidence_analytics["by_dominant_tier"]
    if et:
        print("  evidence-tier Brier:",
              {t: v["mean_brier"] for t, v in et.items()},
              f"(low-tier share {evidence_analytics['low_tier_share']})")
    print("Now run the REFLECTION role in Claude Code over that file.")


def cmd_lesson(args) -> None:
    memory_store.append_lesson(args.text, category=args.category)
    logger.log_event("lesson", {"text": args.text, "category": args.category})
    print("Lesson appended to memory/lessons.md"
          + (f" (category: {args.category})" if args.category else ""))


def cmd_portfolio(_args) -> None:
    """Show open-position exposure by category & correlated factor, with risk flags."""
    report = portfolio.exposure_report()
    print("\n".join(portfolio.summary_lines(report)))
    logger.log_event("portfolio", {"n_positions": report["n_positions"],
                                    "flags": report["flags"]})


def cmd_notify(args) -> None:
    """Send the Supervisor Decision Memo to Telegram.

    Optional JSON payload (--file / inline / stdin) may carry:
      executive_summary, macro_frame, bet_recommendation, and either
      `pred_ids` (ids to pull from predictions.json) or inline `positions`.
    With no payload, it sends all currently-open POSITION predictions.
    """
    payload = _load_json_input(args.file, args.json, allow_empty=True)

    positions = payload.get("positions")
    if positions is None:
        preds = memory_store.load_predictions()
        ids = payload.get("pred_ids")
        if ids:
            idset = {str(i) for i in ids}
            positions = [p for p in preds if p.get("pred_id") in idset]
        else:
            positions = [p for p in memory_store.open_predictions()
                         if p.get("decision") == "POSITION"]

    msg = notification.build_decision_message(
        executive_summary=payload.get("executive_summary", ""),
        macro_frame=payload.get("macro_frame", ""),
        positions=positions,
        bet_recommendation=payload.get("bet_recommendation", ""),
    )
    print("---- notification preview ----")
    print(msg)
    print("------------------------------")

    if not notification.is_configured():
        print("\nTelegram not configured — printed preview only.")
        print("Add TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID to .env to enable sending.")
        return
    ok, detail = notification.send_message(msg)
    logger.log_event("notify", {"ok": ok, "positions": len(positions),
                                "detail": detail})
    print(f"\n{'✅ Sent to Telegram.' if ok else f'❌ Telegram send FAILED: {detail}'}")
    if not ok:
        sys.exit(1)


def cmd_notify_test(_args) -> None:
    if not notification.is_configured():
        sys.exit("Telegram not configured. Copy .env.example to .env and set "
                 "TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID.")
    ok, detail = notification.send_test()
    logger.log_event("notify_test", {"ok": ok, "detail": detail})
    print("✅ Telegram test sent." if ok else f"❌ Telegram test FAILED: {detail}")
    if not ok:
        sys.exit(1)


def cmd_auto_resolve(args) -> None:
    """Check open predictions against Polymarket and resolve the settled ones."""
    openp = memory_store.open_predictions()
    if not openp:
        print("No open predictions to check.")
        return

    print(f"Checking resolution status of {len(openp)} open prediction(s)…")
    newly, ambiguous, pending, no_id = [], [], 0, 0
    for p in openp:
        mid = p.get("market_id")
        if not mid:
            no_id += 1
            continue
        status = polymarket.check_resolution(str(mid))
        if status["resolved"]:
            if args.dry_run:
                print(f"  [dry-run] would resolve {p['pred_id']} -> "
                      f"{status['outcome']}  ({status['question'][:45]})")
            else:
                stored = memory_store.resolve_prediction(p["pred_id"], status["outcome"])
                logger.log_event("auto_resolve", {
                    "pred_id": p["pred_id"], "outcome": status["outcome"],
                    "yes_price": status["yes_price"]})
                print(f"  resolved {p['pred_id']} -> {status['outcome']} "
                      f"(brier {stored.get('brier') if stored else '?'})")
            newly.append((p, status))
        elif status["closed"]:
            ambiguous.append((p, status))
        else:
            pending += 1

    print(f"\nResolved {len(newly)} · ambiguous-closed {len(ambiguous)} · "
          f"still open {pending} · missing market_id {no_id}")

    if ambiguous:
        print("\nClosed but unclear — resolve manually:")
        for p, s in ambiguous:
            print(f"  python main_agent.py resolve {p['pred_id']} <0|1>   "
                  f"# yes_price={s['yes_price']} {p.get('question', '')[:40]}")

    if newly and not args.dry_run:
        memory_store.recompute_calibration()
        cmd_reflect(args)   # rebuilds data/reflection_packet.json
        print("\nNew resolutions recorded → run the REFLECTION role over "
              "data/reflection_packet.json to learn from them.")


def cmd_research(args) -> None:
    """Manual deep-dive: web + X search, or on-chain / polling modes."""
    query = args.query

    if args.onchain:
        print(f"# On-chain: {query}\n")
        data = research.crypto_onchain(query)
        print(research.format_onchain_md(data))
        logger.log_event("research_onchain", {"query": query,
                                              "sources": data.get("sources", [])})
        return
    if args.polling:
        print(f"# Polling: {query}\n")
        data = research.polling_search(query)
        print(research.format_polling_md(data))
        logger.log_event("research_polling", {"query": query,
                                              "hits": len(data.get("hits", []))})
        return

    backend = ("brave" if config.BRAVE_API_KEY else
               "serpapi" if config.SERPAPI_KEY else "duckduckgo")
    x_backend = ("x-api" if config.X_BEARER_TOKEN else
                 "nitter" if config.NITTER_BASE else "web-fallback")
    print(f"# Research: {query}")
    print(f"_web backend: {backend} · x backend: {x_backend} "
          f"(results cached {config.RESEARCH_CACHE_TTL // 3600}h)_\n")

    bundle = research.gather(query, web_n=args.web, x_n=args.x)
    print(research.format_bundle_md(bundle, web_n=args.web, x_n=args.x))
    logger.log_event("research", {"query": query,
                                  "web": len(bundle.get("web", [])),
                                  "x": len(bundle.get("x", []))})

    if args.open and bundle.get("web"):
        top = bundle["web"][0]["url"]
        print(f"\n## Reading top result: {top}\n")
        page = research.browse_page(top)
        if page.get("ok") and page.get("text"):
            print(f"**{page.get('title') or top}**\n")
            print(page["text"][:args.chars])
        elif page.get("ok"):
            print("_page fetched but had no extractable text "
                  "(likely JavaScript-rendered) — open the URL directly._")
        else:
            print(f"_could not read page: {page.get('error', 'unknown error')}_")


def cmd_backtest(args) -> None:
    """Replay resolved markets to bootstrap calibration and test for edge."""
    ids = [s for s in (args.market_ids or "").split(",") if s.strip()] or None
    print(f"Backtesting strategy='{args.strategy}' over "
          f"{'specific ids' if ids else f'last {args.days}d'} "
          f"(gate: edge≥{args.min_edge}, conf≥{args.min_confidence})…")

    report = backtest.run_backtest(
        days=args.days, strategy=args.strategy, min_edge=args.min_edge,
        min_confidence=args.min_confidence, lead_days=args.lead_days,
        market_ids=ids, limit=args.limit, by_category=args.by_category,
        progress=lambda m: print(f"  {m}"))

    if not report["n_scored"]:
        sys.exit("No markets could be scored (no resolved markets with usable price "
                 "history in range). Try a larger --days or different --market-ids.")

    md = backtest.format_report_md(report)
    config.BACKTEST_REPORT_MD.write_text(md, encoding="utf-8")
    config.BACKTEST_REPORT_JSON.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.log_event("backtest", {"strategy": args.strategy, "days": args.days,
                                  "n_scored": report["n_scored"],
                                  "edge_vs_market": report["edge_vs_market"]})

    print("\n" + "\n".join(backtest.summary_lines(report)))
    cat_block = backtest.category_lines(report)
    if cat_block:
        print("\n".join(cat_block))
    print("\nCandidate lessons:")
    for ln in report["candidate_lessons"]:
        print(f"  - [backtest] {ln}")
    print(f"\nFull report -> {config.BACKTEST_REPORT_MD}  (+ .json)")
    if args.write_lessons:
        for ln in report["candidate_lessons"]:
            memory_store.append_lesson(f"[backtest] {ln}")
        print(f"Appended {len(report['candidate_lessons'])} candidate lessons to "
              "memory/lessons.md")
    else:
        print("(candidate lessons NOT saved — re-run with --write-lessons to keep them)")


def cmd_schema_check(_args) -> None:
    """Probe each critical Polymarket endpoint and report schema drift (manual diagnostic).

    Runs regardless of the APEX_SCHEMA_SENTINEL_ENABLED flag (it is an explicit probe)
    and does NOT send alerts — the throttled Telegram alert is fired by the auto path
    inside tools/polymarket.py during real fetches.
    """
    print(f"Schema sentinel — probing endpoints "
          f"(auto-monitor enabled={config.SCHEMA_SENTINEL_ENABLED}, "
          f"interval={config.SCHEMA_SENTINEL_INTERVAL}s)\n")
    raw = rclosed = None
    samples: list[tuple[str, object]] = []
    try:
        raw = polymarket._get("/markets", {
            "active": "true", "closed": "false", "archived": "false",
            "limit": 5, "order": config.SCAN_ORDER, "ascending": "false"})
        samples.append(("gamma_markets", raw))
    except Exception as exc:  # noqa: BLE001 — diagnostic, report and continue
        print(f"  gamma_markets      : FETCH FAILED ({exc})")
    try:
        rclosed = polymarket._get("/markets", {
            "closed": "true", "archived": "false", "limit": 5,
            "order": "volumeNum", "ascending": "false",
            "volume_num_min": config.MIN_VOLUME})
        samples.append(("resolved_market", rclosed))
    except Exception as exc:  # noqa: BLE001
        print(f"  resolved_market    : FETCH FAILED ({exc})")
    token = None
    for src in (raw, rclosed):
        if isinstance(src, list):
            for m in src:
                toks = polymarket._parse_json_field(m.get("clobTokenIds")) or []
                if toks:
                    token = str(toks[0])
                    break
        if token:
            break
    if token:
        try:
            data = polymarket._get_clob(
                "/prices-history",
                {"market": token, "interval": "max", "fidelity": 60})
            samples.append(("clob_prices_history", data))
        except Exception as exc:  # noqa: BLE001
            print(f"  clob_prices_history: FETCH FAILED ({exc})")
    else:
        print("  clob_prices_history: SKIPPED (no token id sampled)")

    drift = 0
    for endpoint, payload in samples:
        issues = schema_sentinel.inspect(endpoint, payload)
        if issues:
            drift += 1
            print(f"  {endpoint:18}: DRIFT — {'; '.join(issues)}")
        else:
            print(f"  {endpoint:18}: OK")
    print(f"\n{drift} endpoint(s) with drift."
          + ("" if drift else " All critical fields present."))


# --------------------------------------------------------------------------- #
# Arg parsing
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main_agent.py",
        description="ApexMind orchestrator — Python plumbing for a Claude-Code brain.")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="scan Polymarket and build the briefing").set_defaults(
        func=cmd_scan)

    pr = sub.add_parser("record", help="record a prediction (JSON arg, --file, or stdin)")
    pr.add_argument("json", nargs="?", default="-",
                    help="prediction JSON, or '-' / omit to read stdin")
    pr.add_argument("--file", "-f", help="read prediction JSON from a file (best on Windows)")
    pr.set_defaults(func=cmd_record)

    rv = sub.add_parser("resolve", help="mark a prediction resolved")
    rv.add_argument("pred_id")
    rv.add_argument("outcome", type=int, choices=[0, 1], help="1=YES, 0=NO")
    rv.set_defaults(func=cmd_resolve)

    sub.add_parser("status", help="show track record & calibration").set_defaults(
        func=cmd_status)
    sub.add_parser("portfolio", help="open-position exposure & correlation flags").set_defaults(
        func=cmd_portfolio)
    sub.add_parser("reflect", help="build a reflection packet").set_defaults(
        func=cmd_reflect)

    ls = sub.add_parser("lesson", help="append a lesson to memory/lessons.md")
    ls.add_argument("text")
    ls.add_argument("--category", "-c", default=None,
                    help="tag the lesson with a category (Politics, Crypto, …)")
    ls.set_defaults(func=cmd_lesson)

    nt = sub.add_parser("notify", help="send the Decision Memo to Telegram")
    nt.add_argument("json", nargs="?", default=None,
                    help="optional memo JSON, or '-' to read stdin")
    nt.add_argument("--file", "-f", help="read memo JSON from a file (best on Windows)")
    nt.set_defaults(func=cmd_notify)

    sub.add_parser("notify-test", help="send a Telegram test message").set_defaults(
        func=cmd_notify_test)

    ar = sub.add_parser("auto-resolve",
                        help="check Polymarket and resolve settled predictions")
    ar.add_argument("--dry-run", action="store_true",
                    help="report what would resolve without writing")
    ar.set_defaults(func=cmd_auto_resolve)

    sub.add_parser("schema-check",
                   help="probe Polymarket endpoints for upstream schema drift"
                   ).set_defaults(func=cmd_schema_check)

    rs = sub.add_parser("research", help="manual web + X deep-dive (or on-chain/polling)")
    rs.add_argument("query", help="the search query / token / topic (quote it)")
    rs.add_argument("--web", type=int, default=6, help="web results (default 6)")
    rs.add_argument("--x", type=int, default=4, help="X results (default 4)")
    rs.add_argument("--open", action="store_true",
                    help="also read the top web result")
    rs.add_argument("--chars", type=int, default=2500,
                    help="chars of the opened page to print (default 2500)")
    rs.add_argument("--onchain", action="store_true",
                    help="on-chain crypto metrics (DexScreener/Coingecko/funding)")
    rs.add_argument("--polling", action="store_true",
                    help="latest polling aggregates for a political topic")
    rs.set_defaults(func=cmd_research)

    bt = sub.add_parser("backtest",
                        help="replay resolved markets to bootstrap calibration & test edge")
    bt.add_argument("--days", type=int, default=90,
                    help="resolved-market window in days (default 90)")
    bt.add_argument("--strategy", default=config.BACKTEST_STRATEGY,
                    choices=list(backtest.STRATEGIES),
                    help=f"mechanical model (default {config.BACKTEST_STRATEGY})")
    bt.add_argument("--min-edge", "--min_edge", dest="min_edge", type=float,
                    default=config.MIN_EDGE, help="edge gate (default from config)")
    bt.add_argument("--min-confidence", "--min_confidence", dest="min_confidence",
                    type=float, default=config.MIN_CONFIDENCE,
                    help="confidence gate (default from config)")
    bt.add_argument("--lead-days", "--lead_days", dest="lead_days", type=int,
                    default=config.BACKTEST_LEAD_DAYS,
                    help="read crowd price N days before resolution (default 7)")
    bt.add_argument("--limit", type=int, default=config.BACKTEST_MAX_MARKETS,
                    help="max markets to replay (default from config)")
    bt.add_argument("--market-ids", "--market_ids", dest="market_ids", default="",
                    help="comma-separated market ids to replay instead of --days")
    bt.add_argument("--by-category", "--by_category", dest="by_category",
                    action="store_true",
                    help="break Brier/edge/hit-rate down by market category")
    bt.add_argument("--write-lessons", "--write_lessons", dest="write_lessons",
                    action="store_true",
                    help="append candidate lessons to memory/lessons.md")
    bt.set_defaults(func=cmd_backtest)

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
