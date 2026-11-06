"""A forged state.session.path must not steer the stop (residual issue).

Everything lives under ``tmp_path``; no real Kiro home is touched.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

from tombstone import stop


def make_v3(root: Path, name: str, session_id: str) -> Path:
    sess = root / "sessions" / "hash1" / name
    sess.mkdir(parents=True)
    (sess / "session.json").write_text(json.dumps({"id": session_id}), encoding="utf-8")
    (sess / "messages.jsonl").write_text("", encoding="utf-8")
    return sess


def event(session_id: str = "abc"):
    return types.SimpleNamespace(session_id=session_id)


def errors(project: Path) -> str:
    log = project / stop.SIDECAR / "errors.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_forged_dir_outside_sessions_is_ignored(tmp_path):
    kiro_home = tmp_path / "kiro"
    real = make_v3(kiro_home, "sess_abc", "sess_abc")
    project = tmp_path / "proj"
    project.mkdir()
    forged = make_v3(tmp_path / "elsewhere", "sess_evil", "sess_evil")
    state = {"session": {"path": str(forged)}}

    session = stop._stop_session(project, event("abc"), state, kiro_home)

    assert Path(session.path) == real.resolve()
    assert "ignored state.session.path" in errors(project)


def test_forged_json_file_outside_sessions_is_ignored(tmp_path):
    kiro_home = tmp_path / "kiro"
    (kiro_home / "sessions").mkdir(parents=True)
    project = tmp_path / "proj"
    project.mkdir()
    forged = tmp_path / "secret.json"
    forged.write_text("{}", encoding="utf-8")
    state = {"session": {"path": str(forged)}}

    assert stop._stop_session(project, event("none"), state, kiro_home) is None
    assert "ignored state.session.path" in errors(project)


def test_dotdot_escape_is_refused(tmp_path):
    kiro_home = tmp_path / "kiro"
    make_v3(kiro_home, "sess_abc", "sess_abc")
    outside = make_v3(tmp_path / "other", "sess_evil", "sess_evil")
    sneaky = kiro_home / "sessions" / ".." / ".." / "other" / "sessions" / "hash1" / "sess_evil"
    assert sneaky.exists() and outside.exists()
    assert stop.safe_session_path(str(sneaky), kiro_home) is None


def test_symlink_escape_is_refused(tmp_path):
    kiro_home = tmp_path / "kiro"
    (kiro_home / "sessions").mkdir(parents=True)
    outside = make_v3(tmp_path / "other", "sess_evil", "sess_evil")
    link = kiro_home / "sessions" / "sess_link"
    link.symlink_to(outside, target_is_directory=True)
    assert stop.safe_session_path(str(link), kiro_home) is None


def test_inside_paths_are_accepted(tmp_path):
    kiro_home = tmp_path / "kiro"
    v3 = make_v3(kiro_home, "sess_abc", "sess_abc")
    cli = kiro_home / "sessions" / "cli"
    cli.mkdir(parents=True)
    v2 = cli / "u1.json"
    v2.write_text("{}", encoding="utf-8")
    v2l = cli / "u1.jsonl"
    v2l.write_text("", encoding="utf-8")
    assert stop.safe_session_path(str(v3), kiro_home) == v3.resolve()
    assert stop.safe_session_path(str(v2), kiro_home) == v2.resolve()
    assert stop.safe_session_path(str(v2l), kiro_home) == v2l.resolve()


def test_inside_but_wrong_shape_is_refused(tmp_path):
    kiro_home = tmp_path / "kiro"
    plain = kiro_home / "sessions" / "hash1" / "notasession"
    plain.mkdir(parents=True)
    txt = kiro_home / "sessions" / "x.txt"
    txt.write_text("x", encoding="utf-8")
    assert stop.safe_session_path(str(plain), kiro_home) is None
    assert stop.safe_session_path(str(txt), kiro_home) is None
    assert stop.safe_session_path(str(kiro_home / "sessions"), kiro_home) is None


def test_valid_stored_path_is_used_without_logging(tmp_path):
    kiro_home = tmp_path / "kiro"
    real = make_v3(kiro_home, "sess_abc", "sess_abc")
    project = tmp_path / "proj"
    project.mkdir()
    state = {"session": {"path": str(real)}}
    session = stop._stop_session(project, event("zzz"), state, kiro_home)
    assert Path(session.path) == real.resolve()
    assert errors(project) == ""
