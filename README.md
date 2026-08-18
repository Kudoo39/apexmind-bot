# 🧠 ApexMind

A **deep-reasoning, self-improving prediction-market agent** for
[Polymarket](https://polymarket.com), built to run on **Claude Code or Codex**
without changing its forecasting workflow. A signed-in CLI is enough — no model
API key is required for subscription-backed local use.

## The idea in one picture

```
            ┌───────────────────────── PYTHON = "hands" ─────────────────────────┐
            │  Polymarket Gamma API ──► tools/polymarket.py ──► shortlist          │
            │  memory/ (beliefs, predictions, lessons, calibration)               │
            │                  └────────► tools/briefing.py ──► data/briefing.md   │
            └───────────────────────────────┬────────────────────────────────────┘
                                            │ (hand-off)
            ┌───────────────────────────────▼──────── CODING AGENT = "brain" ─────┐
            │  system_prompts.md  →  SUPERVISOR → SPECIALIST → (later) REFLECTION  │
            │  deep reasoning, calibrated probabilities, self-critique            │
            └───────────────────────────────┬────────────────────────────────────┘
                                            │ writes back via CLI
            ┌───────────────────────────────▼────────────────────────────────────┐
            │  main_agent.py record/resolve/lesson  →  memory/ updated  →  learn  │
            └─────────────────────────────────────────────────────────────────────┘
```

The Python layer is **deterministic plumbing**. All probability estimation,
judgement, and learning happen in the **configured agent's reasoning**, steered by the
three role prompts in `system_prompts.md`. That keeps model reasoning on the signed-in
agent subscription instead of adding an API client to this project.

## What "self-improving" means here
1. ApexMind records **every** analysed market (model probability + reasoning).
2. When markets resolve, it **scores** itself (Brier, calibration, edge-vs-market).
3. The **Reflection** role distils mistakes into if-then **lessons** and prunes/updates
   structural **beliefs**, which feed into the next briefing. The loop tightens over time.

## Project structure
```
apexmind-bot/
├── AGENTS.md              # Codex entry point; delegates to the same operating manual
├── CLAUDE.md              # operating manual Claude Code auto-loads each session
├── system_prompts.md      # Supervisor / Specialist / Reflection role prompts
├── config.py              # tunables (filters, edge thresholds, API URLs)
├── main_agent.py          # orchestrator CLI: scan/record/notify/auto-resolve/status/…
├── run_analysis.py        # scheduled run (prepare, or --auto headless)
├── requirements.txt
├── .env.example           # copy to .env and add your Telegram secrets
├── .claude/commands/
│   └── apexmind.md        # /apexmind slash command = one full cycle
├── tools/
│   ├── polymarket.py      # Gamma scanner, shortlist, resolution check, price history
│   ├── memory_store.py    # file-based memory read/write
│   ├── briefing.py        # assembles the context packet for Claude Code
│   ├── notification.py    # Telegram Decision-Memo notifier
│   ├── research.py        # web + X + on-chain + polling search, page reading
│   ├── backtest.py        # replay resolved markets; calibration & edge test
│   ├── portfolio.py       # open-position exposure & correlation flags
│   ├── scoring.py         # Brier, calibration, hit-rate, evidence-tier analytics
│   └── logger.py          # JSON-lines event log
├── memory/                # long-term memory (git-diffable, human-editable)
│   ├── beliefs.json
│   ├── predictions.json
│   ├── calibration.json
│   └── lessons.md
├── data/                  # generated briefings / memos (gitignored)
└── logs/                  # JSON-lines audit trail (gitignored)
```

## Setup
```bash
pip install -r requirements.txt
```
No API keys needed for the core agent. (Optional env overrides live in `config.py`,
e.g. `APEX_MIN_EDGE`, `APEX_MIN_LIQUIDITY`, `APEX_SHORTLIST_SIZE`.)

### Telegram notifications (optional but recommended)
Get the Decision Memo pushed to your phone after every run.

1. **Create a bot:** in Telegram, message **@BotFather** → `/newbot` → copy the token.
2. **Find your chat id:** message your new bot once (say "hi"), then open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and read the
   numeric `"chat":{"id": …}`. (For a group, add the bot and use the negative id.)
3. **Save secrets:** copy `.env.example` → `.env` and fill in:
   ```ini
   TELEGRAM_BOT_TOKEN=123456:ABC-your-token
   TELEGRAM_CHAT_ID=987654321
   ```
   `.env` is gitignored — your tokens never get committed.
4. **Test it:**
   ```bash
   python main_agent.py notify-test
   ```
   You should receive a "✅ ApexMind Telegram test" message. If secrets are missing,
   ApexMind just skips sending — it never crashes.

## How to run it with Codex or Claude Code

The analysis logic is identical for both agents. Select the headless provider in
`.env` when needed:

```ini
APEX_AGENT_PROVIDER=codex   # or claude (default)
```

### Codex

Open the repository in Codex and ask: **"Run the full ApexMind cycle over the latest
briefing."** Codex reads `AGENTS.md`, which delegates to the same operating manual and
cycle definition used by Claude Code. For unattended runs, sign in with
`codex.cmd login`, select `codex` above, then run `python run_analysis.py --auto`.

### Claude Code slash command
Open this folder in Claude Code and type:
```
/apexmind
```
It scans Polymarket, then walks the full Supervisor → Specialist → record cycle,
producing a decision memo. Add a focus, e.g. `/apexmind focus on macro/rates markets`.

### Manual, step by step
1. **Scan** (terminal): `python main_agent.py scan`
   (pulls markets **+ 7-day price history** and builds the briefing)
2. **Reason** (in the configured agent): *"Read system_prompts.md and data/briefing_latest.md,
   then run the Supervisor and Specialist roles and record each call."*
3. **Notify**: write a small `notify.json` (executive_summary, macro_frame,
   bet_recommendation — see `docs/notify.example.json`) and run
   `python main_agent.py notify --file notify.json` to push the Decision Memo
   to Telegram. (`notify.json` is a runtime artifact and gitignored.)
4. **Review**: `python main_agent.py status`
5. **Settle**: `python main_agent.py auto-resolve` — checks Polymarket and resolves
   any of your predictions that have settled (no manual outcome lookup needed).
6. **Learn**: the same command builds `data/reflection_packet.json`; then ask the
   configured agent: *"Run the Reflection role over data/reflection_packet.json and
   update memory."*

### Scheduled / unattended (the daily flywheel)
- **Prepare + notify (recommended).** `apex_daily.py` runs the full daily prep —
  `auto-resolve` (settle any resolved markets) → scan/briefing → and, **when Telegram is
  configured**, a `📡 Briefing ready — N Politics/Geopolitics markets` ping (plus a short
  Decision-Memo summary whenever new POSITIONs were recorded since the last run). It never
  reasons; you open the configured agent and run the ApexMind cycle over the fresh briefing. Register it
  with Windows Task Scheduler (daily, off-peak minute) via the bundled wrapper:
  ```powershell
  schtasks /Create /TN "ApexMind Daily Prepare" /TR "C:\path\to\apexmind-bot\apex_daily.cmd" /SC DAILY /ST 08:57 /F
  ```
  (Runs only while you're logged on — no stored credentials. Glanceable status:
  `data/LAST_PREPARE.txt`; log: `logs/daily_cron.log`.)
- **Autonomous mode** (full headless analysis): `python run_analysis.py --auto` scans
  and drives the selected Claude Code or Codex CLI through the same cycle. Set
  `APEX_AGENT_PROVIDER`; the selected CLI must already be signed in.

## CLI reference
| Command | Purpose |
|---|---|
| `python main_agent.py scan` | scan Polymarket (+ price history), build the briefing |
| `python main_agent.py record '<json>'` | log a prediction (use `--file pred.json` on PowerShell); impossible values (a probability outside [0,1], a bad decision/direction/conviction) are rejected with a clear error |
| `python main_agent.py notify [--file memo.json]` | push the Decision Memo to Telegram (defaults to open POSITIONs) |
| `python main_agent.py notify-test` | send a Telegram test message |
| `python main_agent.py auto-resolve [--dry-run]` | settle predictions from Polymarket + build reflection packet; closed markets whose UMA status is still pending/disputed are held back as ambiguous |
| `python main_agent.py schema-check` | probe Polymarket endpoints for upstream schema drift |
| `python main_agent.py backtest --days 90 [--strategy revert] [--by-category]` | replay resolved markets; bootstrap calibration & test edge |
| `python main_agent.py status` | track record, Brier, calibration, open positions |
| `python main_agent.py portfolio` | open-position exposure by category/factor + correlation flags |
| `python main_agent.py trade-sync --file trades.json --replace` | import actual holdings/closed trades separately from forecasts |
| `python main_agent.py trades --refresh` | refresh held prices and show HOLD/TAKE_PROFIT/EXIT actions |
| `python main_agent.py resolve <id> <0\|1>` | mark a market resolved manually (1=YES, 0=NO) |
| `python main_agent.py reflect` | build the reflection packet |
| `python main_agent.py lesson "<text>"` | append a lesson |
| `python run_analysis.py [--auto]` | scheduled run |

Writes to the book are serialised across processes via `memory/.predictions.lock`:
if the daily task is mid-`auto-resolve`, a concurrent `record`/`revise` waits up to
~10 s and then exits with a clear error instead of corrupting
`memory/predictions.json` (a stale lock from a crashed run is broken automatically).

### The `notify.json` payload
All fields optional — with none, ApexMind notifies your current open POSITIONs.
A copy of this sample lives at `docs/notify.example.json`; the `notify.json` you
write at runtime stays in the repo root and is gitignored.
```json
{
  "executive_summary": "Two edges today, both from resolution-timing detail.",
  "macro_frame": "Risk-on; event markets dominate the board.",
  "bet_recommendation": "Lean NO on the late-stage bill; pass elsewhere.",
  "pred_ids": ["p00012", "p00013"]
}
```
Pass `pred_ids` to notify a specific batch, or an inline `positions` array to send
markets verbatim without reading `predictions.json`.

## Backtesting — bootstrap calibration & test for edge
The full Supervisor/Specialist reasoning runs in Claude Code, so it can't be replayed
in pure Python. Instead the harness replays the **mechanical skeleton** against **real
resolved markets**, with **no look-ahead**:

1. pull cleanly-resolved, *traded* binary markets from the last `--days`;
2. read the crowd's YES price **as it was, a lead time before settlement** (adaptive:
   the later of "70% through the market's life" or `--lead-days` before close —
   reading the settlement price would be cheating);
3. run a transparent **strategy** as a stand-in for the Specialist
   (`revert` *(default — fades recent moves)*, `shrink`, `steepen`, `momentum`, or
   `market` *(no-edge baseline)*);
4. apply the **same gate** as live (`--min-edge`, `--min-confidence`);
5. score vs. the realised outcome with `tools/scoring.py`.

```bash
python main_agent.py backtest --days 90 --strategy revert --by-category
```

It prints (and writes `data/backtest_report.md` + `.json`): mean **Brier vs the crowd
baseline** (`edge_vs_market`), a **calibration curve**, **rolling Brier** over time,
a frictionless **simulated ROI** on the positions, and `[backtest]` **candidate
lessons** from the systematic mistakes (add `--write-lessons` to save them, or
`--market-ids id1,id2` to replay specific markets).

Add **`--by-category`** to break every metric down by market type (Crypto / Politics /
Sports / Economy / Weather / Culture, auto-detected from the question) and get
plain-language insights — e.g. *which categories the crowd prices efficiently
(near-coin-flip "up/down" crypto) versus where structure exists worth a Specialist's
time*.

> It's a **lower bound** on ApexMind's edge — the strategies are deliberately dumb and
> the P&L is frictionless. If no strategy beats the crowd's Brier, that *is* the
> finding: edge must come from reasoning and research, not a price-only rule.

## Design choices
- **File-based memory** (JSON + Markdown): inspectable, diff-able, and readable by
  Claude Code directly — no database, no vector store to babysit.
- **Roles are separated** so generation, judgement, and learning don't contaminate
  each other (a classic source of overconfidence).
- **Calibration is first-class**: ApexMind is graded against the *market itself*, so
  "beating the crowd" is measurable, not vibes.

## Disclaimer
ApexMind is a **research and reasoning tool**, not financial advice and not an
automated trading bot. It places no orders and moves no money. Markets are hard and
the crowd is smart — treat every output as a hypothesis to scrutinise.
