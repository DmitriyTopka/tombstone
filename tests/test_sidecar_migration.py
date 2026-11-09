"""Runtime state lives in ``.tombstone/``, not ``.kiro/tombstone/`` (R12.1, R12.2)."""

from __future__ import annotations

import io
import json

from tombstone import cli, sidecar, stop, tombstones


def _project(tmp_path):
    project = tmp_path / "proj"
    (project / ".kiro").mkdir(parents=True)
    return project


def _old(project):
    old = project / ".kiro" / "tombstone"
    (old / "autopsy").mkdir(parents=True)
    (old / "state.json").write_text('{"version": 1}', encoding="utf-8")
    (old / "autopsy" / "2026-11-06_1.md").write_text("receipt", encoding="utf-8")
    return old


def test_sidecar_constant():
    assert str(sidecar.SIDECAR) == ".tombstone"
    assert tombstones.TOMBSTONE_DIR == sidecar.SIDECAR


def test_migration_moves_old_sidecar_with_files(tmp_path):
    project = _project(tmp_path)
    _old(project)
    assert sidecar.migrate(project) is True
    assert not (project / ".kiro" / "tombstone").exists()
    assert (project / ".tombstone" / "state.json").read_text() == '{"version": 1}'
    assert (project / ".tombstone" / "autopsy" / "2026-11-06_1.md").read_text() == "receipt"
    # Second run is a no-op.
    assert sidecar.migrate(project) is False


def test_both_present_leaves_old_one_alone(tmp_path):
    project = _project(tmp_path)
    old = _old(project)
    new = project / ".tombstone"
    new.mkdir()
    (new / "state.json").write_text('{"new": true}', encoding="utf-8")
    assert sidecar.migrate(project) is False
    assert (new / "state.json").read_text() == '{"new": true}'
    assert (old / "state.json").read_text() == '{"version": 1}'
    assert (old / "autopsy" / "2026-11-06_1.md").exists()


def test_symlinked_old_sidecar_is_not_followed(tmp_path):
    project = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x", encoding="utf-8")
    (project / ".kiro" / "tombstone").symlink_to(outside, target_is_directory=True)
    assert sidecar.migrate(project) is False
    assert not (project / ".tombstone").exists()
    assert (project / ".kiro" / "tombstone").is_symlink()
    assert (outside / "keep.txt").read_text() == "x"


def test_symlinked_kiro_dir_is_not_followed(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    real = tmp_path / "real_kiro"
    (real / "tombstone").mkdir(parents=True)
    (project / ".kiro").symlink_to(real, target_is_directory=True)
    assert sidecar.migrate(project) is False
    assert (real / "tombstone").is_dir()
    assert not (project / ".tombstone").exists()


def test_first_state_write_migrates(tmp_path):
    project = _project(tmp_path)
    _old(project)
    tombstones.update_json(project / ".tombstone" / "state.json",
                           lambda s: {**s, "seen": 1}, {"version": 1})
    state = json.loads((project / ".tombstone" / "state.json").read_text())
    assert state["seen"] == 1
    assert (project / ".tombstone" / "autopsy" / "2026-11-06_1.md").exists()
    assert not (project / ".kiro" / "tombstone").exists()


def test_cli_command_migrates_once(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _old(project)
    monkeypatch.chdir(project)
    cli.main(["graveyard"])
    assert (project / ".tombstone" / "autopsy" / "2026-11-06_1.md").exists()
    assert not (project / ".kiro" / "tombstone").exists()


def test_no_code_path_writes_under_old_location(tmp_path, monkeypatch):
    project = _project(tmp_path)
    (project / "tombstones.json").write_text('{"version": 1, "tombstones": []}')
    monkeypatch.chdir(project)
    monkeypatch.setattr("sys.stdin", io.StringIO(
        json.dumps({"cwd": str(project), "prompt": "go"})))
    assert cli.main(["hook", "prompt"]) == 0
    stop.write_stop(project, "over_cap", "TOMBSTONE: stop")
    assert cli.main(["graveyard"]) == 0
    tombstones.reconcile_log(project, "line")
    assert (project / ".tombstone" / "STOP").exists()
    assert not (project / ".kiro" / "tombstone").exists()
    assert sorted(p.name for p in (project / ".kiro").iterdir()) == []


def test_purge_removes_new_sidecar(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    project = _project(tmp_path)
    side = project / ".tombstone"
    (side / "autopsy").mkdir(parents=True)
    (side / "autopsy" / "a.md").write_text("x", encoding="utf-8")
    (side / "STOP").write_text("{}", encoding="utf-8")
    cli.main(["uninstall", "--project", str(project), "--purge"])
    assert not side.exists()


def test_init_names_the_new_sidecar_for_gitignore(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli, "_run_load_command", lambda command: (0, ""))
    project = _project(tmp_path)
    cli.main(["init", "--project", str(project)])
    out = capsys.readouterr().out
    assert ".tombstone/" in out
    assert ".kiro/tombstone" not in out
    assert not (project / ".gitignore").exists()
