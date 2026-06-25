# ApexMind Memory

This folder is ApexMind's long-term memory. It is plain text on purpose: git-diffable,
human-editable, and directly readable by Claude Code in a session.

| File | What it is | Written by |
|------|-----------|-----------|
| `beliefs.json` | Structural world-model beliefs with confidence in [0,1] | Reflection role (via Claude Code) |
| `predictions.json` | Append-only track record of every analysed market | `main_agent.py record` / `resolve` |
| `calibration.json` | Cached calibration report (Brier, per-band gaps) | recomputed by `main_agent.py status`/`resolve` |
| `lessons.md` | If-then lessons distilled from mistakes | `main_agent.py lesson` / Reflection role |

## Rules of hygiene
- **Prune more than you add.** A smaller, sharper belief set beats a sprawling one.
- **Lessons are rules, not diary entries.** Each should be applicable by a future Specialist.
- **Never reward luck.** The Reflection role must separate `Lucky/Unlucky` from `Hit/Miss`.
- Prefer the CLI over hand-editing `predictions.json` so derived fields (edge, brier) stay consistent.
