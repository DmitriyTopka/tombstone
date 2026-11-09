"""Task 6.1: state file and project change detection."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tombstone import watcher

NOW = datetime(2026, 11, 5, 9, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 11, 5, 9, 5, 0, tzinfo=timezone.utc)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git missing")


def _write(path: Path, text: str, mtime: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _git_init(project: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)


# --- state file ------------------------------------------------------------


def test_read_state_defaults_without_file(tmp_path):
    state = watcher.read_state(tmp_path)
    assert state["version"] == 1
    assert state["price_window"] == []
    assert state["warnings"] == []
    assert not (tmp_path / ".kiro").exists()


def test_update_state_roundtrip_and_fields(tmp_path):
    watcher.update_state(tmp_path, lambda s: s.update(avg_request_price=0.05))
    stored = json.loads(watcher.state_path(tmp_path).read_text())
    assert stored["avg_request_price"] == 0.05
    for key in ("session", "turn", "current_task", "tasks_snapshot",
                "last_project_change", "project_fingerprint", "last_event",
                "price_window", "v3_project_hash", "cli_version", "warnings"):
        assert key in stored
    assert watcher.read_state(tmp_path)["avg_request_price"] == 0.05


def test_update_state_keeps_unknown_keys(tmp_path):
    path = watcher.state_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "extra": 7}))
    watcher.update_state(tmp_path, lambda s: s.update(v3_project_hash="abc"))
    stored = json.loads(path.read_text())
    assert stored["extra"] == 7
    assert stored["v3_project_hash"] == "abc"


def test_update_state_stores_home_as_tilde(tmp_path):
    home = tmp_path / "home"
    session = home / ".kiro" / "sessions" / "h" / "sess_1"
    project = tmp_path / "proj"
    project.mkdir()

    def fn(state):
        watcher.set_session(state, "sess_1", "v3", session, home)
        state["warnings"].append(f"bad value in {home}/x.toml")

    watcher.update_state(project, fn, home)
    text = watcher.state_path(project).read_text()
    assert str(home) not in text
    stored = json.loads(text)
    assert stored["session"]["path"] == "~/.kiro/sessions/h/sess_1"
    assert stored["warnings"] == ["bad value in ~/x.toml"]


def test_invalid_state_file_is_left_untouched(tmp_path):
    path = watcher.state_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert watcher.read_state(tmp_path)["version"] == 1
    watcher.update_state(tmp_path, lambda s: s.update(avg_request_price=1.0))
    assert path.read_text() == "{not json"
    assert (path.parent / "pending.jsonl").exists()


def test_tilde_helpers():
    home = Path("/Users/someone")
    assert watcher.to_tilde("/Users/someone", home) == "~"
    assert watcher.to_tilde("/Users/someone/a/b", home) == "~/a/b"
    assert watcher.to_tilde("/Users/someone2/a", home) == "/Users/someone2/a"
    assert watcher.from_tilde("~/a/b", home) == "/Users/someone/a/b"
    assert watcher.from_tilde("~", home) == "/Users/someone"
    assert watcher.from_tilde("/etc/x", home) == "/etc/x"


# --- ignore rules ----------------------------------------------------------


@pytest.mark.parametrize("rel", [
    ".tombstone/state.json",
    ".tombstone/STOP",
    ".git/index",
    "sub/.git/HEAD",
    "tombstones.json",
])
def test_ignored_paths(rel):
    assert watcher.is_ignored(rel)


@pytest.mark.parametrize("rel", [
    "src/a.py",
    ".kiro/specs/x/tasks.md",
    "docs/tombstones.json.md",
    ".gitignore",
])
def test_not_ignored_paths(rel):
    assert not watcher.is_ignored(rel)


# --- fingerprint without git (tree walk) -----------------------------------


def _no_git(monkeypatch):
    monkeypatch.setattr(watcher, "git_changed_files", lambda project: None)


def test_walk_fingerprint_changes_on_edit_and_size(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    _write(tmp_path / "a.py", "x = 1\n", mtime=1_000_000)
    first = watcher.project_fingerprint(tmp_path)
    assert watcher.project_fingerprint(tmp_path) == first
    _write(tmp_path / "a.py", "x = 22\n", mtime=1_000_000)  # size only
    second = watcher.project_fingerprint(tmp_path)
    assert second != first
    _write(tmp_path / "a.py", "x = 22\n", mtime=2_000_000)  # mtime only
    assert watcher.project_fingerprint(tmp_path) != second


def test_walk_fingerprint_sees_new_and_deleted_files(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    _write(tmp_path / "a.py", "a", mtime=1_000_000)
    base = watcher.project_fingerprint(tmp_path)
    _write(tmp_path / "b.py", "b", mtime=1_000_000)
    with_b = watcher.project_fingerprint(tmp_path)
    assert with_b != base
    (tmp_path / "b.py").unlink()
    assert watcher.project_fingerprint(tmp_path) == base


def test_walk_ignores_tombstone_dirs_and_files(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    _write(tmp_path / "a.py", "a", mtime=1_000_000)
    base = watcher.project_fingerprint(tmp_path)
    _write(tmp_path / ".tombstone" / "state.json", "{}")
    _write(tmp_path / ".tombstone" / "autopsy" / "x.md", "x")
    _write(tmp_path / ".git" / "HEAD", "ref")
    _write(tmp_path / "tombstones.json", "{}")
    _write(tmp_path / "node_modules" / "m" / "i.js", "j")
    _write(tmp_path / ".venv" / "lib" / "s.py", "s")
    _write(tmp_path / "pkg" / "__pycache__" / "m.pyc", "c")
    assert watcher.project_fingerprint(tmp_path) == base
    # Other files under .kiro still count.
    _write(tmp_path / ".kiro" / "specs" / "s" / "tasks.md", "- [ ] 1 x")
    assert watcher.project_fingerprint(tmp_path) != base


# --- fingerprint with git --------------------------------------------------


@needs_git
def test_git_fingerprint_follows_changed_files(tmp_path):
    _git_init(tmp_path)
    _write(tmp_path / "a.py", "a", mtime=1_000_000)
    assert watcher.git_changed_files(tmp_path) == ["a.py"]
    first = watcher.project_fingerprint(tmp_path)
    _write(tmp_path / "a.py", "aaa", mtime=1_000_000)
    assert watcher.project_fingerprint(tmp_path) != first


@needs_git
def test_git_fingerprint_ignores_tombstone_files(tmp_path):
    _git_init(tmp_path)
    _write(tmp_path / "a.py", "a", mtime=1_000_000)
    base = watcher.project_fingerprint(tmp_path)
    _write(tmp_path / ".tombstone" / "state.json", "{}")
    _write(tmp_path / "tombstones.json", "{}")
    listed = watcher.git_changed_files(tmp_path)
    assert ".tombstone/state.json" in listed
    assert "tombstones.json" in listed
    assert watcher.project_fingerprint(tmp_path) == base


@needs_git
def test_git_rename_origin_is_skipped(tmp_path):
    _git_init(tmp_path)
    _write(tmp_path / "old.py", "content that is long enough\n" * 5)
    subprocess.run(["git", "add", "old.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "m"],
        cwd=tmp_path, check=True,
    )
    subprocess.run(["git", "mv", "old.py", "new.py"], cwd=tmp_path, check=True)
    assert watcher.git_changed_files(tmp_path) == ["new.py"]


def test_git_failure_falls_back_to_walk(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no git")

    monkeypatch.setattr(subprocess, "run", boom)
    _write(tmp_path / "a.py", "a", mtime=1_000_000)
    assert watcher.git_changed_files(tmp_path) is None
    first = watcher.project_fingerprint(tmp_path)
    _write(tmp_path / "a.py", "aa", mtime=1_000_000)
    assert watcher.project_fingerprint(tmp_path) != first


# --- change bookkeeping ----------------------------------------------------


def test_observe_change_rules():
    state = watcher.default_state()
    assert watcher.observe_change(state, "f1", NOW) is False
    assert state["project_fingerprint"] == "f1"
    assert state["last_project_change"] is None
    assert watcher.observe_change(state, "f1", LATER) is False
    assert state["last_project_change"] is None
    assert watcher.observe_change(state, "f2", LATER) is True
    assert state["last_project_change"] == LATER.isoformat()


def test_detect_change_updates_state_file(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    _write(tmp_path / "a.py", "a", mtime=1_000_000)
    first = watcher.detect_change(tmp_path, NOW)
    assert first["last_project_change"] is None
    assert first["project_fingerprint"]
    same = watcher.detect_change(tmp_path, LATER)
    assert same["last_project_change"] is None
    _write(tmp_path / "a.py", "changed", mtime=1_000_000)
    third = watcher.detect_change(tmp_path, LATER)
    assert third["last_project_change"] == LATER.isoformat()
    stored = json.loads(watcher.state_path(tmp_path).read_text())
    assert stored["last_project_change"] == LATER.isoformat()
    # Writing state.json itself is not a project change.
    again = watcher.detect_change(tmp_path, NOW)
    assert again["last_project_change"] == LATER.isoformat()
