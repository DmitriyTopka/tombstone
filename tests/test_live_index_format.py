"""Regression tests from the first live burn test.

The real V3 index line is relative to ``<kiro_home>/sessions`` and carries
``at`` in epoch milliseconds. Earlier fixtures used absolute paths, so the
watcher never found the session.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tombstone import caps, cli, sessions, watcher

HASH = "7cd0873f97851db7"
SESS = "sess_3a850601-e013-45a7-a07e-73b7cfa01d13"
REAL_INDEX_LINE = {
    "op": "add",
    "sessionPath": f"{HASH}/{SESS}",
    "at": 1793955418488,
}
T0 = datetime(2026, 11, 6, 8, 0, 0, tzinfo=timezone.utc)


def _ts(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _event(moment: datetime, **payload) -> str:
    return json.dumps({"id": "x", "timestamp": _ts(moment), "payload": payload})


def _make_home(tmp_path: Path, project: Path, *, index: bool = True) -> Path:
    home = tmp_path / "kiro"
    root = home / "sessions" / HASH / SESS
    root.mkdir(parents=True)
    (root / "session.json").write_text(json.dumps({
        "id": SESS, "modelId": "auto",
        "workspacePaths": [str(project)], "rootPaths": [str(project)],
    }), encoding="utf-8")
    (root / "messages.jsonl").write_text("", encoding="utf-8")
    if index:
        index_dir = home / "session-index"
        index_dir.mkdir(parents=True)
        (index_dir / f"{HASH}.jsonl").write_text(
            json.dumps(REAL_INDEX_LINE, separators=(",", ":")) + "\n",
            encoding="utf-8")
    return home


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / ".kiro").mkdir(parents=True)
    (project / "main.py").write_text("print(1)\n", encoding="utf-8")
    return project


# --- index format ----------------------------------------------------------


def test_relative_session_path_resolves_against_kiro_home_sessions(tmp_path):
    project = _make_project(tmp_path)
    home = _make_home(tmp_path, project)
    found = sessions.find_session(project, None, kiro_home=home)
    assert found == home / "sessions" / HASH / SESS


def test_absolute_session_path_still_accepted(tmp_path):
    project = _make_project(tmp_path)
    home = _make_home(tmp_path, project, index=False)
    index_dir = home / "session-index"
    index_dir.mkdir(parents=True)
    absolute = home / "sessions" / HASH / SESS
    (index_dir / f"{HASH}.jsonl").write_text(json.dumps(
        {"op": "add", "sessionPath": str(absolute), "at": 1793955418488}) + "\n",
        encoding="utf-8")
    assert sessions.find_session(project, None, kiro_home=home) == absolute


def test_index_at_is_epoch_milliseconds(tmp_path):
    home = tmp_path / "kiro"
    index_dir = home / "session-index"
    index_dir.mkdir(parents=True)
    lines = [
        {"op": "add", "sessionPath": "h/sess_old", "at": 1793955418488},
        {"op": "add", "sessionPath": "h/sess_new", "at": 1793955518488},
        {"op": "add", "sessionPath": "h/sess_none"},
    ]
    (index_dir / "h.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    ordered = sessions._v3_index_candidates(home)
    assert [p.name for _s, p in ordered] == ["sess_new", "sess_old", "sess_none"]
    assert ordered[0][0] == 1793955518.488
    assert ordered[-1][0] == 0.0


def test_newest_ms_index_entry_wins_per_project(tmp_path):
    project = _make_project(tmp_path)
    home = _make_home(tmp_path, project)
    newer = home / "sessions" / HASH / "sess_newer"
    newer.mkdir()
    (newer / "session.json").write_text(json.dumps(
        {"id": "sess_newer", "workspacePaths": [str(project)]}), encoding="utf-8")
    (newer / "messages.jsonl").write_text("", encoding="utf-8")
    with (home / "session-index" / f"{HASH}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"op": "add", "sessionPath": f"{HASH}/sess_newer",
                             "at": 1793955518488}) + "\n")
    assert sessions.find_session(project, None, kiro_home=home) == newer


# --- config warnings -------------------------------------------------------


def test_unknown_table_and_key_warn_and_keep_default(tmp_path):
    (tmp_path / "tombstone.toml").write_text(
        "[cap]\ntask = 3\n[caps]\ntask_cap = 3\nspec = 100\n", encoding="utf-8")
    config = caps.load_config(tmp_path)
    assert config.task_cap == caps.DEFAULT_TASK_CAP
    assert config.spec_cap == 100.0
    joined = "\n".join(config.warnings)
    assert "unknown table [cap]" in joined
    assert "unknown key caps.task_cap" in joined


def test_known_keys_give_no_warning(tmp_path):
    (tmp_path / "tombstone.toml").write_text(
        "[caps]\ntask = 3\nspec = 9\n[stall]\nquiet_window_minutes = 5\n"
        "[budget]\navg_request_price = 0.05\nmin_plan_balance = 1\n"
        "[stop]\nkill_grace_seconds = 10\n", encoding="utf-8")
    assert caps.load_config(tmp_path).warnings == ()


# --- hooks store the session id -------------------------------------------


def test_every_hook_with_session_id_stores_it(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    for event in ("prompt", "pre-tool", "post-tool", "stop"):
        sid = f"sess_{event}"
        payload = {"cwd": str(project), "session_id": sid, "prompt": "hello"}
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        if event == "stop":
            from tombstone import tombstones
            monkeypatch.setattr(tombstones, "on_turn_end", lambda *a, **k: None)
        assert cli.main(["hook", event]) == 0
        assert watcher.read_state(project)["hook_session_id"] == sid, event


def test_watcher_prefers_hook_session_id_over_index(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    home = _make_home(tmp_path, project, index=False)  # no index at all
    root = home / "sessions" / HASH / SESS
    # Workspace does not match the project: only the stored id can find it.
    (root / "session.json").write_text(json.dumps(
        {"id": SESS, "workspacePaths": ["/elsewhere"]}), encoding="utf-8")
    (root / "messages.jsonl").write_text("\n".join([
        _event(T0, type="turn_start", turnId="t1", executionId="e"),
        _event(T0 + timedelta(seconds=1), type="assistant", turnId="t1"),
    ]) + "\n", encoding="utf-8")

    cfg = caps.Config(task_cap=3.0)
    watcher.poll_once(project, cfg, T0 + timedelta(seconds=5), home)
    assert watcher.read_state(project)["session"] is None

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"cwd": str(project), "session_id": SESS})))
    assert cli.main(["hook", "prompt"]) == 0
    watcher.poll_once(project, cfg, T0 + timedelta(seconds=6), home)
    state = watcher.read_state(project)
    assert state["session"]["id"] == SESS
    assert state["v3_project_hash"] == HASH


# --- end to end ------------------------------------------------------------


def test_poll_once_finds_real_index_session_and_raises_over_cap(tmp_path):
    project = _make_project(tmp_path)
    home = _make_home(tmp_path, project)
    root = home / "sessions" / HASH / SESS
    cfg = caps.Config(task_cap=3.0, avg_request_price=0.045)

    lines = [_event(T0, type="turn_start", turnId="t1", executionId="e")]
    lines += [_event(T0 + timedelta(seconds=i + 1), type="assistant", turnId="t1")
              for i in range(10)]
    (root / "messages.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert watcher.poll_once(project, cfg, T0 + timedelta(seconds=20), home) is None
    state = watcher.read_state(project)
    assert state["session"] is not None
    assert state["session"]["id"] == SESS
    assert state["v3_project_hash"] == HASH
    assert state["turn"]["requests"] == 10

    # The file grows: 70 units x 0.045 = 3.15 est. credits, over the cap of 3.
    lines += [_event(T0 + timedelta(seconds=i + 11), type="assistant", turnId="t1")
              for i in range(60)]
    (root / "messages.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    event = watcher.poll_once(project, cfg, T0 + timedelta(seconds=90), home)
    assert event is not None
    assert event.kind == "over_cap"
    assert event.estimate > 3.0
    assert event.requests == 70
    # Raised once per turn.
    assert watcher.poll_once(project, cfg, T0 + timedelta(seconds=95), home) is None
