"""Symlink and forged-record attacks against init and uninstall.

Everything lives under ``tmp_path``. A cloned repo may ship ``.kiro`` (or a
subdirectory) as a symlink and its own ``installed.json``; nothing outside the
project may be written or deleted.
"""

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
    monkeypatch.setattr(cli, "_run_load_command", lambda command: (0, ""))
    return tmp_path, home, project


def _snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*"))}


def test_symlinked_kiro_nothing_deleted_outside(env, capsys):
    tmp, home, project = env
    outside = tmp / "victim"
    (outside / "hooks").mkdir(parents=True)
    (outside / "hooks" / "tombstone-x.json").write_text("{}", encoding="utf-8")
    (outside / "steering").mkdir()
    (outside / "steering" / "tombstone.md").write_text(cli.build_steering_text(),
                                                         encoding="utf-8")
    (project / ".kiro").symlink_to(outside, target_is_directory=True)
    before = _snapshot(outside)
    capsys.readouterr()
    code = cli.main(["uninstall", "--project", str(project), "--force"])
    out = capsys.readouterr().out
    assert code == 1
    assert cli.REFUSED in out
    assert _snapshot(outside) == before


def test_symlinked_kiro_init_writes_nothing_outside(env, capsys):
    tmp, home, project = env
    outside = tmp / "victim"
    outside.mkdir()
    (project / ".kiro").symlink_to(outside, target_is_directory=True)
    code = cli.main(["init", "--project", str(project), "--no-watcher-unit"])
    out = capsys.readouterr().out
    assert code == 1
    assert cli.REFUSED in out
    assert list(outside.rglob("*")) == []
    assert (project / "tombstone.toml").exists()  # the rest continues


def test_symlinked_sidecar_purge_deletes_nothing(env, capsys):
    tmp, home, project = env
    outside = tmp / "victim"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious", encoding="utf-8")
    (project / ".kiro").mkdir()
    (project / ".kiro" / "tombstone").symlink_to(outside, target_is_directory=True)
    capsys.readouterr()
    code = cli.main(["uninstall", "--project", str(project), "--purge"])
    out = capsys.readouterr().out
    assert code == 1
    assert cli.REFUSED in out
    assert (outside / "keep.txt").read_text() == "precious"
    assert (project / ".kiro" / "tombstone").is_symlink()


def test_symlink_inside_sidecar_is_not_followed_on_purge(env, capsys):
    tmp, home, project = env
    outside = tmp / "victim"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious", encoding="utf-8")
    sidecar = project / ".kiro" / "tombstone"
    sidecar.mkdir(parents=True)
    (sidecar / "plain.txt").write_text("x", encoding="utf-8")
    (sidecar / "link").symlink_to(outside, target_is_directory=True)
    (sidecar / "flink").symlink_to(outside / "keep.txt")
    capsys.readouterr()
    code = cli.main(["uninstall", "--project", str(project), "--purge"])
    out = capsys.readouterr().out
    assert code == 1 and cli.REFUSED in out
    assert (outside / "keep.txt").read_text() == "precious"
    assert not (sidecar / "plain.txt").exists()
    assert (sidecar / "link").is_symlink()


def test_forged_installed_json_does_not_authorise_delete(env, capsys):
    tmp, home, project = env
    hooks = project / ".kiro" / "hooks"
    hooks.mkdir(parents=True)
    mine = hooks / "tombstone-stop.json"
    mine.write_text("user content\n", encoding="utf-8")
    sidecar = project / ".kiro" / "tombstone"
    sidecar.mkdir()
    (sidecar / "installed.json").write_text(json.dumps({
        "version": 1,
        "files": {".kiro/hooks/tombstone-stop.json": cli._sha_text("user content\n")},
    }), encoding="utf-8")
    capsys.readouterr()
    cli.main(["uninstall", "--project", str(project)])
    out = capsys.readouterr().out
    assert mine.read_text() == "user content\n"
    assert "changed since init" in out


def test_symlinked_hook_file_refused(env, capsys):
    tmp, home, project = env
    target = tmp / "victim.json"
    target.write_text("{}", encoding="utf-8")
    hooks = project / ".kiro" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "tombstone-x.json").symlink_to(target)
    capsys.readouterr()
    code = cli.main(["uninstall", "--project", str(project), "--force"])
    out = capsys.readouterr().out
    assert code == 1 and cli.REFUSED in out
    assert target.read_text() == "{}"


def test_symlinked_mcp_json_refused(env, capsys):
    tmp, home, project = env
    target = tmp / "other-mcp.json"
    text = json.dumps({"mcpServers": {"tombstone": {"command": "x"}}})
    target.write_text(text, encoding="utf-8")
    settings = project / ".kiro" / "settings"
    settings.mkdir(parents=True)
    (settings / "mcp.json").symlink_to(target)
    capsys.readouterr()
    code = cli.main(["uninstall", "--project", str(project), "--force"])
    assert code == 1
    assert target.read_text() == text


def test_normal_purge_still_works(env, capsys):
    tmp, home, project = env
    assert cli.main(["init", "--project", str(project)]) == 0
    sidecar = project / ".kiro" / "tombstone"
    (sidecar / "autopsy").mkdir(parents=True)
    (sidecar / "autopsy" / "a.md").write_text("x", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["uninstall", "--project", str(project), "--purge"]) == 0
    assert not sidecar.exists()
