"""Tests for ``tombstone uninstall`` (task 10.4). Fake HOME, no real launchctl."""

from __future__ import annotations

import json

import pytest

from tombstone import cli


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    calls: list[list[str]] = []

    def fake_run(command):
        calls.append(list(command))
        return 0, ""

    monkeypatch.setattr(cli, "_run_load_command", fake_run)
    return home, project, calls


def _init(project):
    assert cli.main(["init", "--project", str(project)]) == 0


def _unit(project, home):
    return cli._unit_target(project.resolve(), home)[0]


def test_init_records_hashes(env):
    home, project, _ = env
    _init(project)
    files = cli._load_installed(project)
    assert ".kiro/hooks/tombstone-pre-tool.json" in files
    assert "tombstone.toml" in files and "unit" in files and "mcp-entry" in files
    assert all(len(v) == 64 for v in files.values())


def test_uninstall_removes_init_files_and_unit(env, capsys):
    home, project, calls = env
    _init(project)
    calls.clear()
    capsys.readouterr()
    assert cli.main(["uninstall", "--project", str(project)]) == 0
    out = capsys.readouterr().out
    assert len(calls) == 1
    assert calls[0][0] in ("launchctl", "systemctl")
    assert "bootout" in calls[0] or "disable" in calls[0]
    assert not _unit(project, home).exists()
    assert list((project / ".kiro" / "hooks").glob("tombstone-*.json")) == []
    assert not (project / ".kiro" / "steering" / "tombstone.md").exists()
    assert not (project / ".kiro" / "settings" / "mcp.json").exists()
    assert (project / "tombstone.toml").exists()
    assert "kept" in out and "tombstone.toml" in out
    assert "removed" in out


def test_uninstall_twice_is_safe(env, capsys):
    home, project, calls = env
    _init(project)
    cli.main(["uninstall", "--project", str(project)])
    calls.clear()
    capsys.readouterr()
    assert cli.main(["uninstall", "--project", str(project)]) == 0
    out = capsys.readouterr().out
    assert calls == []
    assert "not found" in out and "removed" not in out


def test_mcp_keeps_other_servers(env):
    home, project, _ = env
    mcp = project / ".kiro" / "settings" / "mcp.json"
    mcp.parent.mkdir(parents=True)
    mcp.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "k": 1}),
                   encoding="utf-8")
    _init(project)
    assert "tombstone" in json.loads(mcp.read_text())["mcpServers"]
    cli.main(["uninstall", "--project", str(project)])
    data = json.loads(mcp.read_text())
    assert data == {"mcpServers": {"other": {"command": "x"}}, "k": 1}


def test_changed_file_kept_unless_force(env, capsys):
    home, project, _ = env
    _init(project)
    steering = project / ".kiro" / "steering" / "tombstone.md"
    steering.write_text("mine\n", encoding="utf-8")
    capsys.readouterr()
    cli.main(["uninstall", "--project", str(project)])
    out = capsys.readouterr().out
    assert steering.read_text() == "mine\n"
    assert f"kept {steering} (changed since init" in out
    cli.main(["uninstall", "--project", str(project), "--force"])
    assert not steering.exists()


def test_works_without_installed_json(env):
    home, project, _ = env
    _init(project)
    cli.install_record_path(project.resolve()).unlink()
    hook = project / ".kiro" / "hooks" / "tombstone-stop.json"
    hook.write_text("{}\n", encoding="utf-8")
    cli.main(["uninstall", "--project", str(project)])
    assert hook.exists()  # differs from the builder output: changed, kept
    assert not (project / ".kiro" / "hooks" / "tombstone-pre-tool.json").exists()
    assert not (project / ".kiro" / "steering" / "tombstone.md").exists()
    assert not _unit(project, home).exists()


def test_only_own_unit_is_removed(env):
    home, project, _ = env
    _init(project)
    other = _unit(project, home).with_name("com.tombstone.other-12345678.plist"
                                           if cli.sys.platform == "darwin"
                                           else "tombstone-other-12345678.service")
    other.write_text("x", encoding="utf-8")
    cli.main(["uninstall", "--project", str(project)])
    assert other.exists()
    assert not _unit(project, home).exists()


def test_purge_keeps_tombstones_json(env):
    home, project, _ = env
    _init(project)
    ledger = project / "tombstones.json"
    ledger.write_text('{"tombstones": []}', encoding="utf-8")
    autopsy = project / ".kiro" / "tombstone" / "autopsy"
    autopsy.mkdir(parents=True)
    (autopsy / "a.md").write_text("x", encoding="utf-8")
    cli.main(["uninstall", "--project", str(project)])
    assert (autopsy / "a.md").exists() and (project / "tombstone.toml").exists()
    cli.main(["uninstall", "--project", str(project), "--purge"])
    assert ledger.exists()
    assert not (project / ".kiro" / "tombstone").exists()
    assert not (project / "tombstone.toml").exists()
