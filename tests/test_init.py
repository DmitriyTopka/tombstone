"""Tests for ``tombstone init`` (task 10.2). No real launchctl or systemctl."""

from __future__ import annotations

import json
import plistlib
import sys

import pytest

from tombstone import cli


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "my proj"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    calls: list[list[str]] = []

    def fake_load(command):
        calls.append(list(command))
        return 0, ""

    monkeypatch.setattr(cli, "_run_load_command", fake_load)
    return home, project, calls


def test_init_creates_files_and_loads_unit(env, capsys):
    home, project, calls = env
    assert cli.main(["init", "--project", str(project)]) == 0
    out = capsys.readouterr().out
    assert (project / "tombstone.toml").is_file()
    assert (project / ".kiro" / "steering" / "tombstone.md").is_file()
    assert not (project / ".kiro" / "agents").exists()
    assert (project / ".kiro" / "settings" / "mcp.json").is_file()
    assert (project / ".kiro" / "hooks" / "tombstone-pre-tool.json").is_file()
    assert "created " in out
    assert "kiro-cli chat --agent tombstone" not in out
    assert "init --v2" in out
    assert "V3 (default)" in out
    assert len(calls) == 1
    if sys.platform == "darwin":
        assert calls[0][:2] == ["launchctl", "bootstrap"]
        assert calls[0][2].startswith("gui/")
        plists = list((home / "Library" / "LaunchAgents").glob("com.tombstone.*.plist"))
        assert len(plists) == 1
        data = plistlib.loads(plists[0].read_bytes())
        assert data["ProgramArguments"][1:] == ["-m", "tombstone", "watch",
                                                "--project", str(project.resolve())]
        assert data["RunAtLoad"] is True and data["KeepAlive"] is True
    else:
        assert calls[0][:4] == ["systemctl", "--user", "enable", "--now"]
        units = list((home / ".config" / "systemd" / "user").glob("tombstone-*.service"))
        assert len(units) == 1
        assert "watch" in units[0].read_text(encoding="utf-8")


def test_init_twice_leaves_files_unchanged(env, capsys):
    _home, project, _calls = env
    cli.main(["init", "--project", str(project)])
    capsys.readouterr()
    toml = project / "tombstone.toml"
    toml.write_text("# edited\n", encoding="utf-8")
    assert cli.main(["init", "--project", str(project)]) == 0
    out = capsys.readouterr().out
    assert toml.read_text(encoding="utf-8") == "# edited\n"
    assert "created " not in out.split("loaded watcher")[0]
    assert f"exists, left unchanged {toml}" in out


def test_no_watcher_unit_writes_and_loads_nothing(env, capsys):
    home, project, calls = env
    assert cli.main(["init", "--project", str(project), "--no-watcher-unit"]) == 0
    out = capsys.readouterr().out
    assert calls == []
    assert list(home.rglob("*")) == []
    assert "-m tombstone watch --project" in out
    assert str(project.resolve()) in out
    assert (project / "tombstone.toml").is_file()


def test_init_uses_sys_executable_in_hooks_and_mcp(env):
    _home, project, _calls = env
    cli.main(["init", "--project", str(project), "--no-watcher-unit"])
    hook = json.loads((project / ".kiro" / "hooks" / "tombstone-pre-tool.json")
                      .read_text("utf-8"))["hooks"][0]["action"]["command"]
    assert hook.startswith(f"'{sys.executable}' -m tombstone hook pre-tool --project '")
    mcp = json.loads((project / ".kiro" / "settings" / "mcp.json").read_text("utf-8"))
    assert mcp["mcpServers"]["tombstone"] == {
        "command": sys.executable,
        "args": ["-m", "tombstone", "mcp", "--project", str(project.resolve())]}


def test_init_merges_existing_mcp_json(env, capsys):
    _home, project, _calls = env
    path = project / ".kiro" / "settings" / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"x": {"command": "x"}}}), encoding="utf-8")
    cli.main(["init", "--project", str(project), "--no-watcher-unit"])
    assert f"updated {path}" in capsys.readouterr().out
    servers = json.loads(path.read_text("utf-8"))["mcpServers"]
    assert set(servers) == {"x", "tombstone"}
    cli.main(["init", "--project", str(project), "--no-watcher-unit"])
    assert "already registered" in capsys.readouterr().out


def test_init_v2_flag_writes_agent_config(env, capsys):
    _home, project, _calls = env
    cli.main(["init", "--project", str(project), "--no-watcher-unit", "--v2"])
    assert (project / ".kiro" / "agents" / "tombstone.json").is_file()
    assert "kiro-cli chat --agent tombstone" in capsys.readouterr().out


def test_init_notes_existing_agent_config_without_touching_it(env, capsys):
    _home, project, _calls = env
    agent = project / ".kiro" / "agents" / "tombstone.json"
    agent.parent.mkdir(parents=True)
    agent.write_text("{}", encoding="utf-8")
    cli.main(["init", "--project", str(project), "--no-watcher-unit"])
    assert "note: " in capsys.readouterr().out
    assert agent.read_text("utf-8") == "{}"


def test_load_failure_prints_command_and_exits_one(env, capsys, monkeypatch):
    _home, project, _calls = env
    monkeypatch.setattr(cli, "_run_load_command", lambda c: (5, "boom"))
    assert cli.main(["init", "--project", str(project)]) == 1
    out = capsys.readouterr().out
    assert "could not load" in out and "boom" in out
    assert "watch --project" in out


def test_only_command_actions_and_no_model_key(env):
    _home, project, _calls = env
    cli.main(["init", "--project", str(project), "--no-watcher-unit", "--v2"])
    for path in (project / ".kiro" / "hooks").glob("tombstone-*.json"):
        for hook in json.loads(path.read_text(encoding="utf-8"))["hooks"]:
            assert hook["action"]["type"] == "command"
    agent = json.loads((project / ".kiro" / "agents" / "tombstone.json").read_text("utf-8"))
    assert "model" not in agent


def test_unit_builders_quote_paths_with_spaces(tmp_path):
    project = tmp_path / "a b"
    project.mkdir()
    unit = cli.build_systemd_unit("/usr/bin/tombstone", project)
    assert f'"{project.resolve()}"' in unit
    assert "Restart=always" in unit
    assert cli.unit_id(project).startswith("a-b-")
