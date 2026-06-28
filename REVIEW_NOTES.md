# REVIEW_NOTES.md

Operational notes for reviewers/operators of ApexMind — the *why* behind monitoring
and guardrails that aren't obvious from the code alone.

---

## 2026-06-29 — Hardening the "hands": data-integrity, durability & a test suite

A code review found concrete durability/integrity bugs in the deterministic Python
layer (the *hands*) and no automated tests. This pass fixes them without touching the
reasoning layer (the *brain*) or the file-based, hand-editable book. **Every fix ships
with a test that fails before and passes after; the full suite (`python -m pytest -q`)
is green at 42 tests.** A 4-dimension adversarial multi-agent review of the diff
(data-integrity, invariants/CLI-compat, calibration/no-look-ahead math, tests/P2)
returned **zero confirmed defects**.

### What changed & why

- **Atomic writes (`tools/atomic_io.py`, new).** `memory_store._save` (the book +
  calibration + beliefs), `briefing.py` (briefing md + markets cache), and the
  reflection-packet / backtest reports now serialise to a temp file in the same dir,
  `fsync`, then `os.replace()` — an atomic rename on POSIX **and** Windows. *Why:* a
  crash or full disk mid-`write_text()` truncated-then-wrote could empty the entire
  track record. *Verified:* `test_atomic_writes.py` monkeypatches `os.replace` to raise
  and asserts the original file is left intact (not truncated) with no temp-file litter.

- **Collision-proof prediction ids (`memory_store._next_pred_id`).** Next id is
  `max(numeric pXXXXX) + 1`, not `len(preds)+1`. *Why:* the README invites hand-editing;
  deleting a row made a count-based id collide with a survivor, so every
  `resolve`/`add_evidence`/`reflect` lookup silently hit the wrong row. *Verified:*
  `test_pred_ids.py` reproduces the review case (delete `p00002` → next id is `p00004`,
  all ids distinct) and that `bt-…` ids don't perturb the counter.

- **`supersedes` wired into scoring (`memory_store.superseded_ids` /
  `resolved_predictions`).** One shared definition now excludes superseded re-analyses
  from **both** exposure (`portfolio`) and calibration/track-record (`recompute_
  calibration`, `status`, `reflect`). *Why:* the dedup guard tagged re-analyses, and
  portfolio excluded them, but calibration still double-counted the same bet (biasing it
  toward oft-revisited high-conviction markets). *Verified:* `test_supersedes_scoring.py`
  records → re-records (tagged `supersedes`) → resolves both → `recompute_calibration()
  ["n"] == 1`.

- **Refuse re-resolving a settled row (`resolve_prediction`).** Raises a clear
  `ValueError`; `cmd_resolve` catches it and exits cleanly. `auto-resolve` snapshots
  `open_predictions()` once and only ever resolves OPEN rows, so it never trips the
  guard. *Verified:* `test_resolve_guard.py` asserts the second resolve raises and the
  stored `outcome`/`brier` are unchanged.

- **Guarded in-place `revise(pred_id, patch)` + `revise` CLI verb.** The sanctioned
  edit path for an OPEN row: refuses the protected identity/lifecycle/resolution set
  (`pred_id, created_at, status, supersedes, outcome, brier, resolved_at`) and refuses
  resolved rows; re-derives `edge`; normalises patched evidence. *Why:* the only prior
  guidance was "edit the JSON by hand," which can silently rewrite identity/resolution.
  *Verified:* `test_revise.py` covers normal update + edge recompute, evidence
  normalisation, each protected field raising, a resolved row raising, a non-dict patch
  raising `TypeError`, and missing-id → `None`.

- **Small-sample calibration honesty (`scoring.calibration_report`).** Each bucket now
  carries a **Wilson score 95% CI** (`ci_low`/`ci_high`) on its realised rate and a
  `reliable` flag (`n >= config.MIN_BUCKET_N`, default 8); a top-level `note` tells the
  Reflection role to treat an unreliable bucket's `gap` as noise. **`edge_vs_market` is
  unchanged** — beating the crowd price is the right baseline for a prediction market.
  *Verified:* `test_calibration.py` asserts the CI brackets the realised rate, widens as
  `n` shrinks, `reliable` flips at the threshold, and `edge_vs_market` is preserved
  (exact arithmetic).

- **P2 — research scraper: slimmed (option a).** Removed the dead **Nitter** X backend
  (off by default; most public instances are defunct) from `research.py`, `config.py`,
  `main_agent.py`, `.env.example`, and the manual's backend line. Kept Brave/SerpAPI
  (keyed) → DuckDuckGo (keyless press-tier *fallback*) for web and X-API → site:x.com
  web fallback for X, and documented that interactive runs should prefer native
  WebSearch/WebFetch. *Why keep DDG:* the headless path (`run_analysis.py --auto`,
  briefing enrichment) has no native search, so removing the only keyless web backend
  would leave it blind; it already degrades to `[]`. *Verified:* `test_research_offline.py`
  asserts `web_search`/`x_search`/`gather`/`browse_page` return well-formed empty (never
  raise) when the network is down, and the DDG parser tolerates empty/garbage HTML.

- **Test harness (`tests/`, `requirements-dev.txt`).** `conftest.py` has an autouse
  fixture that repoints every `config` path at a throwaway tmp dir, so tests never touch
  the real `memory/`/`data/`. Also covers `reconcile_ledger` (incl. ignoring
  bool/≤0 LRs), `suggest_confidence_ceiling` thresholds, and a **no-look-ahead**
  assertion for the backtest snapshot selector (`_nearest_at_or_before`): truncating
  future bars leaves the chosen snapshot identical, and `simulate_market`'s snapshot is
  strictly ≥30 min before resolution.

### Decisions / descopes (with reasons)

- **Skipped the Brier skill-score / reliability-resolution-uncertainty decomposition**
  (the optional half of P1.6). It's redundant here: `edge_vs_market` already scores
  against the *stronger* baseline (the tradeable crowd price); a second base-rate
  baseline invites confusion without adding a decision the Reflection role would make
  differently.

### Honest residual gaps (deferred, with reasons)

- **Not all writers are atomic.** The research cache (`tools/research.py`, already
  swallows `OSError`), the schema-sentinel throttle state, and the `apex_daily` diff
  state are regenerated/disposable and were left as direct writes — corruption there is
  self-healing on the next run, unlike the book. Easy to migrate later if wanted.
- **`mkstemp` perms.** `atomic_io` creates the temp via `tempfile.mkstemp` (mode 0600),
  so on POSIX a freshly-replaced file becomes owner-only — irrelevant on the Windows
  host here, but a POSIX deployment that needs group-readable books would want an
  explicit `chmod`. Not addressed (no such requirement today).
- **`revise` is single-row, last-write-wins.** No optimistic-concurrency check; fine for
  a single-operator local book, but two concurrent `revise`/`record` processes could
  still race (the atomic write keeps each individual file write all-or-nothing, but not
  a read-modify-write across processes). Out of scope for this pass.

---

## Schema Sentinel (`tools/schema_sentinel.py`)

### What problem it solves
ApexMind parses two upstream APIs whose payloads it does **not** control:
Polymarket **Gamma** (`/markets`) and the **CLOB** (`/prices-history`). If Polymarket
renames or drops a field, nothing throws — `_normalise()` / `prepare_resolved()` just
return `None`, markets get silently filtered out, the shortlist empties, and the
backtest scores nothing. **Silent degradation is the risk.** The sentinel turns that
silent failure into one clear alert.

### What it watches
A representative item from each critical response must still carry the fields the
pipeline depends on:

| endpoint (`schema_sentinel` key) | required fields | any-of (fallbacks) |
|---|---|---|
| `gamma_markets` (active scan) | `id`, `question`, `outcomes`, `outcomePrices`, `clobTokenIds`, `endDate` | (`volumeNum`\|`volume`), (`liquidityNum`\|`liquidity`) |
| `resolved_market` (backtest pool) | `id`, `question`, `outcomes`, `outcomePrices`, `clobTokenIds` | (`closedTime`\|`endDate`) |
| `clob_prices_history` | top-level `history` (list); each point has `t`, `p` | — |

The expected shapes live in `EXPECTED` in `tools/schema_sentinel.py` — update that dict
if the pipeline starts depending on a new field.

### How it behaves (the contract)
- **OFF by default** — gated on `config.SCHEMA_SENTINEL_ENABLED` (`APEX_SCHEMA_SENTINEL_ENABLED`).
- **Pure monitoring** — `check()` never raises into its caller and never mutates the
  payload or business logic. Any internal error is swallowed. An **empty result is
  treated as healthy** (a legit empty list is not drift — we never false-alarm on zero
  rows; only a present-but-reshaped payload triggers).
- **Throttled** — at most **one alert per endpoint per `SCHEMA_SENTINEL_INTERVAL`**
  (default 86400s = 1 day), persisted in `data/.schema_sentinel_state.json`. The alert
  goes out via `notification.send_message` (no-op if Telegram isn't configured), is
  printed to stdout (so it lands in `logs/daily_cron.log`), and is recorded via
  `logger.log_event("schema_drift", …)`.
- **Cheap** — validates only the first representative item; a no-op when disabled.

### Where it hooks in (auto path)
`tools/polymarket.py` calls `schema_sentinel.check(...)` right after each raw fetch:
`scan_markets` → `gamma_markets`; `fetch_resolved_markets` → `resolved_market`;
`price_history_range` / `price_history_full` → `clob_prices_history`. These are the
fetches the daily flywheel (`apex_daily.py`) and the backtest already exercise, so no
extra calls are added.

### Manual probe
`python main_agent.py schema-check` fetches one live sample per endpoint and prints
`OK` / `DRIFT — …` for each. It runs regardless of the flag and does **not** send
alerts (it's a diagnostic) — use it to verify the expected shapes still match reality.

### How to silence
- It is **already silent by default** (flag off → zero behaviour, zero overhead).
- If enabled and you want to mute it: set `APEX_SCHEMA_SENTINEL_ENABLED=false` in `.env`
  (or unset it), or globally mute Telegram with `APEX_NOTIFY_ENABLED=false` (keeps the
  stdout/log record but stops the phone alert).
- To snooze a *single* endpoint after a confirmed-benign change: it self-throttles for
  `SCHEMA_SENTINEL_INTERVAL`; to extend, raise `APEX_SCHEMA_SENTINEL_INTERVAL`. To reset
  the throttle, delete `data/.schema_sentinel_state.json`.
- After a real drift, fix the field mapping in `tools/polymarket.py` (`_normalise` /
  `prepare_resolved`) and update `EXPECTED` to match the new shape.
