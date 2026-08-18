"""Provider selection changes only the headless CLI transport."""

from __future__ import annotations

import pytest

import config
import run_analysis


def test_claude_command_preserves_existing_path(monkeypatch):
    monkeypatch.setattr(config, "AGENT_PROVIDER", "claude")
    monkeypatch.setattr(config, "CLAUDE_CLI", "claude.cmd")

    label, command = run_analysis.agent_command("do the cycle")

    assert label == "Claude Code"
    assert command == ["claude.cmd", "-p", "do the cycle"]


def test_codex_command_uses_noninteractive_workspace(monkeypatch):
    monkeypatch.setattr(config, "AGENT_PROVIDER", "codex")
    monkeypatch.setattr(config, "CODEX_CLI", "codex.cmd")

    label, command = run_analysis.agent_command("do the cycle")

    assert label == "Codex"
    assert command == [
        "codex.cmd", "--ask-for-approval", "never", "--search", "exec",
        "--sandbox", "workspace-write", "do the cycle",
    ]


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "AGENT_PROVIDER", "other")

    with pytest.raises(ValueError, match="APEX_AGENT_PROVIDER"):
        run_analysis.agent_command()
