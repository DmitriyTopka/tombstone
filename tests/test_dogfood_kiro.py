"""Task 17.1: the repo's own .kiro/ holds the full V3 hook set and the MCP entry."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tombstone.cli import _V3_HOOKS

ROOT = Path(__file__).resolve().parent.parent
KIRO = ROOT / ".kiro"


def test_one_hook_per_v3_trigger() -> None:
    hooks_dir = KIRO / "hooks"
    expected = {f"tombstone-{stem}.json" for stem, _, _, _ in _V3_HOOKS}
    assert {p.name for p in hooks_dir.glob("tombstone-*.json")} == expected
    for stem, trigger, event, _ in _V3_HOOKS:
        data = json.loads((hooks_dir / f"tombstone-{stem}.json").read_text(encoding="utf-8"))
        assert len(data["hooks"]) == 1
        hook = data["hooks"][0]
        assert hook["trigger"] == trigger
        assert hook["action"]["type"] == "command"
        assert hook["action"]["command"] == f".venv/bin/tombstone hook {event}"


def test_mcp_json_starts_tombstone_mcp() -> None:
    data = json.loads((KIRO / "settings" / "mcp.json").read_text(encoding="utf-8"))
    entry = data["mcpServers"]["tombstone"]
    assert entry["command"] == ".venv/bin/tombstone"
    assert entry["args"] == ["mcp"]


def test_kiro_files_not_gitignored() -> None:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("git or repository not available")
    files = [str(p.relative_to(ROOT)) for p in KIRO.rglob("*") if p.is_file()]
    assert files
    for name in files:
        res = subprocess.run(["git", "check-ignore", "-q", name], cwd=ROOT,
                             capture_output=True, text=True)
        assert res.returncode == 1, f"{name} is matched by .gitignore"
