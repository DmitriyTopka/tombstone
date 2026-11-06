"""Tests for the power folder, CLI version check and hook --project (task 10.3)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tombstone import caps, cli, tombstones, watcher

_REAL_PROBE = watcher.installed_cli_version
POWER = Path(__file__).resolve().parent.parent / "power"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_json_fields():
    data = _load(POWER / "plugin.json")
    assert data["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert data["name"] == "tombstone" and data["license"] == "MIT"
    assert set(data["author"]) == {"name", "url"}
    for key in ("version", "description", "keywords", "homepage", "repository"):
        assert data[key]


def test_mcp_json():
    data = _load(POWER / "mcp.json")
    assert data["$schema"] == "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    assert data["mcpServers"]["tombstone"] == {
        "type": "stdio", "command": "/path/to/python",
        "args": ["-m", "tombstone", "mcp"]}


def test_skill_frontmatter_and_body():
    text = (POWER / "skills" / "tombstone" / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"---\nname: tombstone\ndescription: (.+)\n---\n", text)
    assert match
    assert "get_budget" in text and "TOMBSTONE:" in text
    assert "power/hooks/" in text and "tombstone init" in text


def test_power_files_match_builders(tmp_path):
    expected = cli.build_power_files(POWER)
    for path, text in expected.items():
        assert path.read_text(encoding="utf-8") == text, path
    on_disk = {p for p in POWER.rglob("*") if p.is_file()}
    assert on_disk == set(expected)


def test_power_hooks_are_command_only_without_model():
    for path in (POWER / "hooks").rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert '"model"' not in text and '"type": "agent"' not in text
        assert "'/path/to/python' -m tombstone hook " in text


def test_no_dash_characters_in_power():
    for path in POWER.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert "\u2014" not in text and "\u2013" not in text, path


def test_write_power_folder_roundtrip(tmp_path):
    written = cli.write_power_folder(tmp_path / "power")
    assert len(written) == len(cli.build_power_files(tmp_path / "power"))
    assert (tmp_path / "power" / "hooks" / "v3" / "tombstone-stop.json").is_file()


# --- default price ---------------------------------------------------------


def test_default_avg_request_price():
    assert caps.DEFAULT_AVG_REQUEST_PRICE == 0.25
    assert tombstones.DEFAULT_AVG_REQUEST_PRICE == 0.25


# --- CLI version -----------------------------------------------------------


def test_tested_version_constant():
    assert watcher.TESTED_CLI_VERSION == "3.1.0"


def test_check_matching_version_no_warning(tmp_path):
    assert watcher.check_cli_version(tmp_path, installed="3.1.0") is None
    state = watcher.read_state(tmp_path)
    assert state["cli_version"] == {"installed": "3.1.0", "tested": "3.1.0"}


def test_check_mismatch_warns_once_and_stores(tmp_path):
    warning = watcher.check_cli_version(tmp_path, installed="3.1.2")
    assert warning and "3.1.2" in warning and "3.1.0" in warning
    assert "\n" not in warning
    assert watcher.read_state(tmp_path)["cli_version"]["installed"] == "3.1.2"


def test_check_unknown_version_no_warning(tmp_path):
    assert watcher.check_cli_version(tmp_path, probe=lambda: None) is None
    assert watcher.read_state(tmp_path)["cli_version"]["installed"] is None


def test_installed_cli_version_parses_output(monkeypatch):
    import subprocess

    class Done:
        stdout = "kiro-cli 3.0.0\n"

    monkeypatch.setattr(watcher, "installed_cli_version", _REAL_PROBE)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done())
    assert watcher.installed_cli_version() == "3.0.0"

    def missing(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    assert watcher.installed_cli_version() is None


def test_init_stores_version_and_warns(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(watcher, "installed_cli_version", lambda: "9.9.9")
    project = tmp_path / "p"
    project.mkdir()
    assert cli.main(["init", "--project", str(project), "--no-watcher-unit"]) == 0
    out = capsys.readouterr().out
    assert out.count("warning: kiro-cli 9.9.9") == 1
    assert watcher.read_state(project)["cli_version"]["installed"] == "9.9.9"


def test_init_matching_version_prints_no_warning(tmp_path, capsys):
    project = tmp_path / "p"
    project.mkdir()
    cli.main(["init", "--project", str(project), "--no-watcher-unit"])
    assert "warning: kiro-cli" not in capsys.readouterr().out
    assert watcher.read_state(project)["cli_version"]["installed"] == "3.1.0"


def test_watch_start_checks_version(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(watcher, "installed_cli_version", lambda: "2.0.0")
    monkeypatch.setattr(watcher, "run", lambda *a, **k: 0)
    project = tmp_path / "p"
    project.mkdir()
    assert cli.main(["watch", "--project", str(project)]) == 0
    err = capsys.readouterr().err
    assert err.count("warning: kiro-cli 2.0.0") == 1
    assert watcher.read_state(project)["cli_version"]["installed"] == "2.0.0"


# --- hook --project for every event ----------------------------------------


@pytest.mark.parametrize("event", ["pre-tool", "post-tool", "prompt", "stop",
                                   "session-start", "task-pre", "task-post"])
def test_hook_project_override_for_every_event(event, tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    seen = []
    monkeypatch.setattr(cli, "_read_stdin_json",
                        lambda: {"cwd": str(tmp_path / "elsewhere")})
    handlers = dict(cli._HOOK_EVENTS)
    handlers[event] = lambda stdin: seen.append(cli._hook_project_root(stdin)) or 0
    monkeypatch.setattr(cli, "_HOOK_EVENTS", handlers)
    assert cli.main(["hook", event, "--project", str(project)]) == 0
    assert seen == [project]
    assert cli._PROJECT_OVERRIDE is None


def test_hook_pre_tool_blocks_in_override_project(tmp_path, monkeypatch, capsys):
    from tombstone import stop

    project = tmp_path / "proj"
    (project / ".kiro").mkdir(parents=True)
    other = tmp_path / "other"
    (other / ".kiro").mkdir(parents=True)
    stop.write_stop(project, "cap", "TOMBSTONE: test stop")
    monkeypatch.setattr(cli, "_read_stdin_json", lambda: {"cwd": str(other)})
    assert cli.main(["hook", "pre-tool", "--project", str(project)]) == 2
    assert "TOMBSTONE" in capsys.readouterr().err
    assert cli.main(["hook", "pre-tool"]) == 0


def test_hook_without_project_uses_stdin_cwd(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    (project / ".kiro").mkdir(parents=True)
    monkeypatch.setattr(cli, "_read_stdin_json", lambda: {"cwd": str(project)})
    assert cli._PROJECT_OVERRIDE is None
    assert cli._hook_project_root({"cwd": str(project)}) == project.resolve()
