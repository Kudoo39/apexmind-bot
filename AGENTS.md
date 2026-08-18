# ApexMind Codex Instructions

`CLAUDE.md` is the canonical ApexMind operating manual. Read and follow it in
full before running an analysis cycle. References to Claude Code in that manual
mean the active reasoning agent; do not change the Supervisor, Specialist,
Reflection, research, decision, recording, notification, or memory workflows.

For a full interactive cycle, follow `.claude/commands/apexmind.md` exactly. The
file is provider-neutral despite its location. On Windows, prefer the repository
virtual environment when available:

```powershell
.\.venv\Scripts\python.exe main_agent.py <command>
```

Never overwrite or discard existing changes in `memory/`. Record and revise
predictions only through the documented CLI paths.
