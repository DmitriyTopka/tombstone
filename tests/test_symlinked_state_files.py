"""A symlink planted inside a real ``.kiro/tombstone`` must never be written.

Each test points one sidecar file (lock, pending queue, state, error log,
STOP, usage cache, graveyard, autopsy) or one init target (mcp.json, a hook
file) at a file outside the project, runs the code that would write it and
checks the outside file is byte-for-byte unchanged.
"""

from __future__ import annotations

import io
import json
import os
import stat

import pytest

from tombstone import autopsy, cli, safepath, stop, tombstones

OUTSIDE_TEXT = b"precious user data, do not touch\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    side = project / ".kiro" / "tombstone"
    side.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(OUTSIDE_TEXT)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli, "_run_load_command", lambda command: (0, ""))
    return project, side, outside


def _unchanged(outside):
    assert outside.read_bytes() == OUTSIDE_TEXT


def _prompt_hook(monkeypatch, project) -> int:
    payload = {"cwd": str(project), "prompt": "do the thing"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return cli.main(["hook", "prompt"])


def test_symlinked_lock_file(env, monkeypatch):
    project, side, outside = env
    (side / "state.json.lock").symlink_to(outside)
    assert _prompt_hook(monkeypatch, project) == 0
    _unchanged(outside)
    assert (side / "state.json.lock").is_symlink()


def test_symlinked_lock_file_dangling(env, monkeypatch, tmp_path):
    project, side, _outside = env
    target = tmp_path / "not_yet_there"
    (side / "state.json.lock").symlink_to(target)
    assert _prompt_hook(monkeypatch, project) == 0
    assert not target.exists()


def test_symlinked_state_json(env, monkeypatch):
    project, side, outside = env
    (side / "state.json").symlink_to(outside)
    assert _prompt_hook(monkeypatch, project) == 0
    _unchanged(outside)
    assert (side / "state.json").is_symlink()


def test_symlinked_pending_jsonl_on_append(env, monkeypatch):
    project, side, outside = env
    (side / "state.json").write_text("{ not json", encoding="utf-8")
    (side / "pending.jsonl").symlink_to(outside)
    assert _prompt_hook(monkeypatch, project) == 0
    _unchanged(outside)
    assert (side / "state.json").read_text(encoding="utf-8") == "{ not json"


def test_symlinked_pending_jsonl_on_drain(env, monkeypatch):
    project, side, outside = env
    line = json.dumps({"target": "state.json", "change": {"x": 1}})
    outside.write_bytes((line + "\n").encode())
    before = outside.read_bytes()
    (side / "pending.jsonl").symlink_to(outside)
    assert _prompt_hook(monkeypatch, project) == 0
    assert outside.read_bytes() == before
    assert (side / "pending.jsonl").is_symlink()


def test_symlinked_errors_log(env, monkeypatch):
    project, side, outside = env
    (side / "errors.log").symlink_to(outside)
    # corrupt state: update_json logs a warning
    (side / "state.json").write_text("{ not json", encoding="utf-8")
    assert _prompt_hook(monkeypatch, project) == 0
    tombstones._log_warning(side, "direct warning")
    stop._log_error(project, "direct error")
    cli._log_hook_error({"cwd": str(project)}, "direct hook error")
    tombstones.reconcile_log(project, "direct reconcile line")
    _unchanged(outside)


def test_symlinked_usage_json(env):
    project, side, outside = env
    (side / "usage.json").symlink_to(outside)
    tombstones.update_json(side / "usage.json", lambda _old: {"a": 1}, {})
    _unchanged(outside)


def test_symlinked_stop_file(env):
    project, side, outside = env
    (side / "STOP").symlink_to(outside)
    stop.write_stop(project, "cap", "blocked")
    assert stop.resume(project) is False
    _unchanged(outside)
    assert (side / "STOP").is_symlink()


def test_symlinked_graveyard_and_autopsy(env):
    project, side, outside = env
    (side / "graveyard.html").symlink_to(outside)
    tombstones.write_graveyard(project)
    _unchanged(outside)
    (side / "autopsy").mkdir()
    receipt = side / "autopsy" / "2026-11-06_x.md"
    receipt.symlink_to(outside)
    assert autopsy.append_actual(receipt, None) is False
    _unchanged(outside)


def test_symlinked_tombstones_json_update(env):
    project, _side, outside = env
    (project / "tombstones.json").symlink_to(outside)
    tombstones.update_json(project / "tombstones.json",
                           lambda s: s, tombstones._empty_store())
    _unchanged(outside)


def test_init_with_symlinked_mcp_json_and_hook_file(env, capsys):
    project, _side, outside = env
    settings = project / ".kiro" / "settings"
    settings.mkdir()
    (settings / "mcp.json").symlink_to(outside)
    hooks = project / ".kiro" / "hooks"
    hooks.mkdir()
    (hooks / "tombstone-stop.json").symlink_to(outside)
    cli.main(["init", "--project", str(project), "--no-watcher-unit"])
    _unchanged(outside)
    assert (settings / "mcp.json").is_symlink()
    assert (hooks / "tombstone-stop.json").is_symlink()


def test_uninstall_with_symlinked_files(env, capsys):
    project, side, outside = env
    settings = project / ".kiro" / "settings"
    settings.mkdir()
    (settings / "mcp.json").symlink_to(outside)
    hooks = project / ".kiro" / "hooks"
    hooks.mkdir()
    (hooks / "tombstone-stop.json").symlink_to(outside)
    (side / "state.json.lock").symlink_to(outside)
    for flags in ([], ["--force"], ["--purge", "--force"]):
        cli.main(["uninstall", "--project", str(project), *flags])
        _unchanged(outside)


def test_merge_mcp_settings_symlink_swapped_in_after_check(env):
    """write_atomic itself refuses a symlink target, guard or not."""
    project, _side, outside = env
    link = project / ".kiro" / "mcp_link.json"
    link.symlink_to(outside)
    with pytest.raises(OSError):
        safepath.write_atomic(link, "{}")
    with pytest.raises(OSError):
        safepath.append_nofollow(link, "x")
    with pytest.raises(OSError):
        safepath.open_nofollow(link, "w")
    with pytest.raises(OSError):
        safepath.open_nofollow(link, "a")
    _unchanged(outside)


def test_open_nofollow_does_not_truncate_lock_and_refuses_hardlink(tmp_path):
    lock = tmp_path / "x.lock"
    lock.write_bytes(b"keep")
    with safepath.open_nofollow(lock, "a"):
        pass
    assert lock.read_bytes() == b"keep"
    victim = tmp_path / "victim"
    victim.write_bytes(b"data")
    hard = tmp_path / "hard"
    os.link(victim, hard)
    with pytest.raises(OSError):
        safepath.open_nofollow(hard, "w")
    assert victim.read_bytes() == b"data"


def test_open_nofollow_refuses_fifo(tmp_path):
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(OSError):
        safepath.open_nofollow(fifo, "a")


def test_write_atomic_keeps_mode_and_leaves_no_temp(tmp_path):
    target = tmp_path / "f.json"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o640)
    safepath.write_atomic(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["f.json"]
