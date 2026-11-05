"""Tests for session discovery and open-turn progress (task 1.3).

These build a fake ``kiro_home`` tree under ``tmp_path`` so no real home,
clock or network is needed. The V3 layout is
``<home>/sessions/<hash>/sess_<id>/{session.json,messages.jsonl}`` with an
index at ``<home>/session-index/<hash>.jsonl``. The V2 layout is
``<home>/sessions/cli/<uuid>.json`` plus ``<uuid>.jsonl``.
"""

from __future__ import annotations

import json
from pathlib import Path

from tombstone.sessions import (
    Progress,
    find_session,
    open_turn_progress,
    v3_project_hash,
)

PROJECT = "/workspace/project0"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e, separators=(",", ":")) for e in events) + "\n",
        encoding="utf-8",
    )


def _v3_session(home: Path, project_hash: str, sess_id: str, *,
                workspace: str = PROJECT, events: list[dict] | None = None,
                index_at: str = "2026-11-05T17:38:00Z") -> Path:
    """Create a V3 session on disk and index it. Return its sess_ dir."""
    root = home / "sessions" / project_hash / f"sess_{sess_id}"
    _write(root / "session.json", json.dumps({
        "id": f"sess_{sess_id}",
        "modelId": "auto",
        "workspacePaths": [workspace],
        "rootPaths": [workspace],
    }))
    if events is None:
        events = [
            {"timestamp": "2026-11-05T17:37:56Z",
             "payload": {"type": "turn_start", "turnId": "t1"}},
            {"timestamp": "2026-11-05T17:37:58Z",
             "payload": {"type": "assistant", "turnId": "t1"}},
        ]
    _jsonl(root / "messages.jsonl", events)
    index = home / "session-index" / f"{project_hash}.jsonl"
    _append_index(index, {"op": "upsert", "sessionPath": str(root), "at": index_at})
    return root


def _append_index(index: Path, line: dict) -> None:
    index.parent.mkdir(parents=True, exist_ok=True)
    with index.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, separators=(",", ":")) + "\n")


def _v2_session(home: Path, uuid: str, *, cwd: str = PROJECT,
                updated_at: str = "2026-11-03T08:52:23Z",
                closed_requests: int = 24,
                jsonl_events: list[dict] | None = None) -> Path:
    """Create a V2 cli session on disk. Return its .json path."""
    json_path = home / "sessions" / "cli" / f"{uuid}.json"
    _write(json_path, json.dumps({
        "session_id": uuid,
        "cwd": cwd,
        "updated_at": updated_at,
        "session_state": {
            "conversation_metadata": {
                "user_turn_metadatas": [
                    {"total_request_count": closed_requests,
                     "metering_usage": [{"unit": "credit", "value": 1.0}],
                     "model": "auto",
                     "end_timestamp": updated_at,
                     "turn_duration": {"secs": 10, "nanos": 0}},
                ],
            },
        },
    }))
    if jsonl_events is None:
        jsonl_events = []
    _jsonl(json_path.with_suffix(".jsonl"), jsonl_events)
    return json_path


# --- find_session: V3 by id -------------------------------------------------

def test_find_v3_by_session_id_glob(tmp_path):
    home = tmp_path / ".kiro"
    root = _v3_session(home, "14e984af5081f39b", "aaaa")
    found = find_session(Path(PROJECT), session_id="sess_aaaa", kiro_home=home)
    assert found == root


def test_find_v3_by_id_without_sess_prefix(tmp_path):
    home = tmp_path / ".kiro"
    root = _v3_session(home, "deadbeef", "bbbb")
    found = find_session(Path(PROJECT), session_id="bbbb", kiro_home=home)
    assert found == root


def test_find_v3_by_id_uses_cached_hash_first(tmp_path):
    home = tmp_path / ".kiro"
    root = _v3_session(home, "cachedhash00", "cccc")
    found = find_session(Path(PROJECT), session_id="cccc", kiro_home=home,
                         v3_project_hash="cachedhash00")
    assert found == root
    # A wrong cached hash still falls back to the glob.
    found2 = find_session(Path(PROJECT), session_id="cccc", kiro_home=home,
                          v3_project_hash="wronghash")
    assert found2 == root


# --- find_session: V3 by project via the index -----------------------------

def test_find_v3_by_project_newest_index_wins(tmp_path):
    home = tmp_path / ".kiro"
    _v3_session(home, "h1", "old", index_at="2026-11-05T10:00:00Z")
    newer = _v3_session(home, "h2", "new", index_at="2026-11-05T12:00:00Z")
    found = find_session(Path(PROJECT), kiro_home=home)
    assert found == newer


def test_find_v3_skips_sessions_for_other_projects(tmp_path):
    home = tmp_path / ".kiro"
    _v3_session(home, "other", "xxxx", workspace="/workspace/elsewhere",
                index_at="2026-11-05T20:00:00Z")
    mine = _v3_session(home, "mine", "yyyy", index_at="2026-11-05T09:00:00Z")
    found = find_session(Path(PROJECT), kiro_home=home)
    assert found == mine


# --- find_session: V2 by cwd ------------------------------------------------

def test_find_v2_by_cwd_newest_updated_at(tmp_path):
    home = tmp_path / ".kiro"
    _v2_session(home, "old-uuid", updated_at="2026-11-03T08:00:00Z")
    newer = _v2_session(home, "new-uuid", updated_at="2026-11-03T09:00:00Z")
    found = find_session(Path(PROJECT), kiro_home=home)
    assert found == newer


def test_find_v2_skips_other_cwd(tmp_path):
    home = tmp_path / ".kiro"
    _v2_session(home, "elsewhere", cwd="/workspace/other")
    found = find_session(Path(PROJECT), kiro_home=home)
    assert found is None


# --- find_session: both engines, newest activity wins ----------------------

def test_find_prefers_newest_activity_across_engines(tmp_path):
    home = tmp_path / ".kiro"
    v3_root = _v3_session(home, "hh", "v3one")
    v2_json = _v2_session(home, "v2one")
    # Make the V2 jsonl the newest touched file.
    import os
    import time
    later = time.time() + 100
    os.utime(v2_json.with_suffix(".jsonl"), (later, later))
    found = find_session(Path(PROJECT), kiro_home=home)
    assert found == v2_json
    # Now make the V3 messages.jsonl newer.
    later2 = later + 100
    os.utime(v3_root / "messages.jsonl", (later2, later2))
    found2 = find_session(Path(PROJECT), kiro_home=home)
    assert found2 == v3_root


def test_find_returns_none_when_nothing_matches(tmp_path):
    home = tmp_path / ".kiro"
    (home / "sessions").mkdir(parents=True)
    assert find_session(Path(PROJECT), kiro_home=home) is None


def test_find_never_raises_on_missing_home(tmp_path):
    # kiro_home that does not exist at all.
    assert find_session(Path(PROJECT), kiro_home=tmp_path / "nope") is None
    assert find_session(Path(PROJECT), session_id="zzz",
                        kiro_home=tmp_path / "nope") is None


# --- v3_project_hash --------------------------------------------------------

def test_v3_project_hash_from_path(tmp_path):
    home = tmp_path / ".kiro"
    root = _v3_session(home, "14e984af5081f39b", "hashme")
    assert v3_project_hash(root) == "14e984af5081f39b"
    assert v3_project_hash(root / "messages.jsonl") == "14e984af5081f39b"


def test_v3_project_hash_none_for_non_v3(tmp_path):
    assert v3_project_hash(tmp_path / "random.txt") is None


# --- open_turn_progress: V3 -------------------------------------------------

def test_open_progress_v3_counts_open_assistant_events(tmp_path):
    home = tmp_path / ".kiro"
    events = [
        {"payload": {"type": "turn_start", "turnId": "t1"}},
        {"payload": {"type": "assistant", "turnId": "t1"}},
        {"payload": {"type": "assistant", "turnId": "t1"}},
        {"payload": {"type": "assistant", "turnId": "t1"}},
    ]
    root = _v3_session(home, "h", "openv3", events=events)
    prog = open_turn_progress(root)
    assert isinstance(prog, Progress)
    assert prog.requests_so_far == 3
    assert prog.size_bytes > 0
    assert prog.mtime > 0


def test_open_progress_v3_ignores_closed_turn_assistants(tmp_path):
    home = tmp_path / ".kiro"
    events = [
        # A closed first turn with two assistant events.
        {"payload": {"type": "turn_start", "turnId": "t1"}},
        {"payload": {"type": "assistant", "turnId": "t1"}},
        {"payload": {"type": "assistant", "turnId": "t1"}},
        {"payload": {"type": "usage_summary", "turnId": "t1",
                     "promptTurnSummaries": [{"unit": "credit", "usage": 0.1}],
                     "requestIds": ["r1", "r2"]}},
        # An open second turn with one assistant event so far.
        {"payload": {"type": "turn_start", "turnId": "t2"}},
        {"payload": {"type": "assistant", "turnId": "t2"}},
    ]
    root = _v3_session(home, "h", "mixed", events=events)
    prog = open_turn_progress(root)
    assert prog is not None
    assert prog.requests_so_far == 1


def test_open_progress_v3_zero_when_no_open_turn(tmp_path):
    home = tmp_path / ".kiro"
    events = [
        {"payload": {"type": "turn_start", "turnId": "t1"}},
        {"payload": {"type": "assistant", "turnId": "t1"}},
        {"payload": {"type": "usage_summary", "turnId": "t1",
                     "promptTurnSummaries": [{"unit": "credit", "usage": 0.1}],
                     "requestIds": ["r1"]}},
    ]
    root = _v3_session(home, "h", "closed", events=events)
    prog = open_turn_progress(root)
    assert prog is not None
    assert prog.requests_so_far == 0


# --- open_turn_progress: V2 -------------------------------------------------

def test_open_progress_v2_counts_assistant_lines_beyond_closed(tmp_path):
    home = tmp_path / ".kiro"
    # One closed turn with 2 requests, and a jsonl with 3 assistant lines:
    # 2 belong to the closed turn, 1 is the live one.
    jsonl_events = [
        {"role": "user"},
        {"role": "assistant"},
        {"role": "assistant"},
        {"role": "user"},
        {"role": "assistant"},
    ]
    json_path = _v2_session(home, "v2prog", closed_requests=2,
                            jsonl_events=jsonl_events)
    prog = open_turn_progress(json_path)
    assert prog is not None
    assert prog.requests_so_far == 1
    assert prog.size_bytes > 0


def test_open_progress_v2_handles_nested_message_role(tmp_path):
    home = tmp_path / ".kiro"
    jsonl_events = [
        {"message": {"role": "assistant"}},
        {"message": {"role": "assistant"}},
    ]
    json_path = _v2_session(home, "v2nested", closed_requests=0,
                            jsonl_events=jsonl_events)
    prog = open_turn_progress(json_path)
    assert prog is not None
    assert prog.requests_so_far == 2


# --- open_turn_progress: edges ---------------------------------------------

def test_open_progress_none_for_unknown_path(tmp_path):
    bad = tmp_path / "nope.txt"
    bad.write_text("not a session", encoding="utf-8")
    assert open_turn_progress(bad) is None


def test_open_progress_none_when_jsonl_missing(tmp_path):
    # A V2 json with no sibling jsonl.
    home = tmp_path / ".kiro"
    json_path = home / "sessions" / "cli" / "lonely.json"
    _write(json_path, json.dumps({
        "session_id": "lonely", "cwd": PROJECT,
        "session_state": {"conversation_metadata": {"user_turn_metadatas": []}},
    }))
    assert open_turn_progress(json_path) is None
