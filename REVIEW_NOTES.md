# REVIEW_NOTES.md

Operational notes for reviewers/operators of ApexMind — the *why* behind monitoring
and guardrails that aren't obvious from the code alone.

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
