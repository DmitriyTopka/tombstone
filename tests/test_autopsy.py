"""Autopsy receipt (task 8.1, R7.1 to R7.4). Everything under tmp_path."""

from __future__ import annotations

import json
from pathlib import Path

from tombstone import autopsy, sessions, stop

HOME = Path("/Users/someone")
STOP_TIME = "2026-11-06T12:00:00Z"


def make_session(tmp_path: Path, calls: int = 12, with_summary: bool = False):
    root = tmp_path / "kh" / "sess_abc"
    root.mkdir(parents=True)
    (root / "session.json").write_text(json.dumps({
        "id": "sess_abc", "modelId": "auto",
        "workspacePaths": [str(tmp_path)],
    }), encoding="utf-8")
    events = [{"timestamp": "2026-11-06T11:00:00.000Z",
               "payload": {"type": "turn_start", "turnId": "t1"}}]
    for i in range(calls):
        events.append({"timestamp": f"2026-11-06T11:{i:02d}:10.000Z", "payload": {
            "type": "tool_call", "turnId": "t1", "toolName": f"tool{i}",
            "args": {"path": f"{HOME}/proj/f{i}.py"}, "status": "approved",
            "title": f"Reading {HOME}/proj/f{i}.py" if i % 2 else None}})
    events.append({"timestamp": "2026-11-06T11:59:00.000Z",
                   "payload": {"type": "assistant", "turnId": "t1"}})
    if with_summary:
        events.append({"timestamp": "2026-11-06T12:01:00.000Z", "payload": {
            "type": "usage_summary", "turnId": "t1", "requestIds": ["a", "b"]}})
    (root / "messages.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return sessions.parse_session(root)


def make_project(tmp_path: Path) -> Path:
    spec = tmp_path / ".kiro" / "specs" / "demo"
    spec.mkdir(parents=True)
    (spec / "tasks.md").write_text("- [ ] 2.1 Build the parser\n", encoding="utf-8")
    return tmp_path


def stop_entry(**over) -> dict:
    base = {"reason": "over_cap", "message": "msg from stop entry", "time": STOP_TIME,
            "requests_at_stop": 40, "estimate": 58.0, "task_id": "2.1",
            "spec": "demo", "session_id": "sess_abc", "turn_id": "t1",
            "quiet_minutes": 12.5}
    base.update(over)
    return base


def write_usage(project: Path, balance, fetched_at: str) -> None:
    side = project / ".kiro" / "tombstone"
    side.mkdir(parents=True, exist_ok=True)
    (side / "usage.json").write_text(json.dumps(
        {"fetched_at": fetched_at, "balance": balance, "reason": None}), encoding="utf-8")


def test_scrub_replaces_home_and_resolved_form(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    text = f"a {link}/x b {real}/y c {link}"
    out = autopsy.scrub(text, link)
    assert str(link) not in out and str(real) not in out
    assert out == "a ~/x b ~/y c ~"


def test_scrub_leaves_longer_names_alone():
    assert autopsy.scrub("/Users/someone2/x", HOME) == "/Users/someone2/x"


def test_write_autopsy_fields_and_path(tmp_path):
    project = make_project(tmp_path)
    write_usage(project, 500.0, "2026-11-06T11:30:00Z")
    stop.write_stop(project, "over_cap", "TOMBSTONE: task 2.1 over budget. Stop.")
    session = make_session(tmp_path)
    path = autopsy.write_autopsy(project, stop_entry(), session, {}, home=HOME)
    assert path == project / ".kiro/tombstone/autopsy/2026-11-06_2-1.md"
    text = path.read_text(encoding="utf-8")
    for needle in ("Task id: 2.1", "Build the parser", "Engine: v3", "Model: auto",
                   "Requests at stop: 40", "(est.): 58.000", "Actual after the turn: pending",
                   "12 minutes", STOP_TIME, "STOP reason: over_cap",
                   "Refusal text: TOMBSTONE: task 2.1 over budget. Stop.",
                   "Step two (kill): no", "500.000 credits"):
        assert needle in text, needle


def test_last_ten_tool_calls_and_scrub(tmp_path):
    project = make_project(tmp_path)
    session = make_session(tmp_path, calls=12)
    path = autopsy.write_autopsy(project, stop_entry(), session, {}, home=HOME)
    text = path.read_text(encoding="utf-8")
    assert "tool0 " not in text and "tool1 " not in text
    assert "tool2 " in text and "tool11 " in text
    assert text.count("| tool") == 10
    assert str(HOME) not in text
    assert "~/proj/f2.py" in text


def test_cells_cut_to_200(tmp_path):
    project = make_project(tmp_path)
    session = make_session(tmp_path, calls=1)
    events = (session.path / "messages.jsonl")
    lines = events.read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[1])
    obj["payload"]["args"] = "x" * 500
    lines[1] = json.dumps(obj)
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")
    text = autopsy.write_autopsy(project, stop_entry(), sessions.parse_session(session.path),
                                 {}, home=HOME).read_text(encoding="utf-8")
    assert "x" * 201 not in text and "x" * 190 in text


def test_unknown_task_uses_turn_id(tmp_path):
    project = make_project(tmp_path)
    path = autopsy.write_autopsy(project, stop_entry(task_id=None, spec=None),
                                 make_session(tmp_path), {}, home=HOME)
    assert path.name == "2026-11-06_unknown-t1.md"


def test_balance_unknown_when_cache_is_newer_than_stop(tmp_path):
    project = make_project(tmp_path)
    write_usage(project, 400.0, "2026-11-06T12:30:00Z")
    text = autopsy.write_autopsy(project, stop_entry(), make_session(tmp_path), {},
                                 home=HOME).read_text(encoding="utf-8")
    assert "Plan balance at stop (est.): unknown" in text


def test_step_two_warning_and_kill(tmp_path):
    project = make_project(tmp_path)
    session = make_session(tmp_path)
    warned = autopsy.write_autopsy(
        project, stop_entry(warnings=["no process found"]), session, {}, home=HOME
    ).read_text(encoding="utf-8")
    assert "skipped: no process found" in warned
    killed = autopsy.write_autopsy(
        project, stop_entry(kill={"status": "killed"}), session, {}, home=HOME
    ).read_text(encoding="utf-8")
    assert "Step two (kill): yes" in killed


def test_append_actual_killed_turn_shows_balance_delta_not_zero(tmp_path):
    project = make_project(tmp_path)
    write_usage(project, 500.0, "2026-11-06T11:30:00Z")
    path = autopsy.write_autopsy(project, stop_entry(), make_session(tmp_path), {}, home=HOME)
    write_usage(project, 488.5, "2026-11-06T12:10:00Z")
    assert autopsy.append_actual(path, None, home=HOME) is True
    text = path.read_text(encoding="utf-8")
    assert "actual: not written by Kiro for a killed turn" in text
    assert "11.500 credits" in text
    assert "change (est.): 0.000" not in text


def test_append_actual_no_balance_is_unknown_never_zero(tmp_path):
    project = make_project(tmp_path)
    path = autopsy.write_autopsy(project, stop_entry(), make_session(tmp_path), {}, home=HOME)
    autopsy.append_actual(path, None, home=HOME)
    tail = path.read_text(encoding="utf-8").split("## Actual cost")[1]
    assert "unknown" in tail and "change (est.): 0.000" not in tail


def test_append_actual_with_real_cost_and_only_once(tmp_path):
    project = make_project(tmp_path)
    session = make_session(tmp_path, with_summary=True)
    path = autopsy.write_autopsy(project, stop_entry(), session, {}, home=HOME)
    turn = sessions.Turn(engine="v3", session_id="s", turn_id="t1", model="auto",
                         requests=4, cost=2.0, start=None, end=None, state="closed")
    assert autopsy.append_actual(path, turn, home=HOME) is True
    assert autopsy.append_actual(path, turn, home=HOME) is False
    text = path.read_text(encoding="utf-8")
    assert text.count("## Actual cost (actual)") == 1
    assert "Actual (actual): 2.000" in text and "0.500" in text


def test_begin_stop_calls_real_autopsy(tmp_path, monkeypatch):
    from tombstone import watcher
    project = make_project(tmp_path)
    session = make_session(tmp_path)
    monkeypatch.setattr(stop, "_stop_session", lambda *a, **k: session)
    event = watcher.Event(
        kind="over_cap", at="2026-11-06T12:00:00+00:00", message="TOMBSTONE: stop.",
        detail="d", project=str(project), task_id="2.1", spec="demo", estimate=58.0,
        requests=40, session_id="sess_abc", turn_id="t1")
    entry = stop.begin_stop(event, project=project, announce_fn=lambda *a, **k: None)
    assert entry["autopsy"] and Path(entry["autopsy"]).is_file()


# --- task 8.2: append the actual cost once --------------------------------


def make_kiro_home_session(tmp_path: Path, priced: bool):
    root = tmp_path / "kh" / "sessions" / "h1" / "sess_abc"
    root.mkdir(parents=True)
    (root / "session.json").write_text(json.dumps({
        "id": "sess_abc", "modelId": "auto", "workspacePaths": [str(tmp_path)],
    }), encoding="utf-8")
    events = [
        {"timestamp": "2026-11-06T11:00:00.000Z",
         "payload": {"type": "turn_start", "turnId": "t1"}},
        {"timestamp": "2026-11-06T11:59:00.000Z",
         "payload": {"type": "assistant", "turnId": "t1"}},
    ]
    if priced:
        events.append({"timestamp": "2026-11-06T12:01:00.000Z", "payload": {
            "type": "usage_summary", "turnId": "t1", "requestIds": ["a", "b", "c", "d"],
            "promptTurnSummaries": [{"unit": "credit", "usage": 2.0}]}})
    (root / "messages.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return tmp_path / "kh"


def set_pending(project: Path, path: Path) -> None:
    side = project / ".kiro" / "tombstone"
    side.mkdir(parents=True, exist_ok=True)
    (side / "state.json").write_text(json.dumps({"autopsy_pending": {
        "path": str(path), "turn_id": "t1", "session_id": "sess_abc"}}), encoding="utf-8")


def read_pending(project: Path):
    data = json.loads((project / ".kiro/tombstone/state.json").read_text(encoding="utf-8"))
    return data.get("autopsy_pending")


def test_settle_appends_once_and_clears_pending(tmp_path):
    project = make_project(tmp_path)
    path = autopsy.write_autopsy(project, stop_entry(), make_session(tmp_path), {}, home=HOME)
    set_pending(project, path)
    kiro_home = make_kiro_home_session(tmp_path, priced=True)
    assert autopsy.settle_autopsy(project, kiro_home=kiro_home, home=HOME) is True
    assert read_pending(project) is None
    assert autopsy.settle_autopsy(project, kiro_home=kiro_home, home=HOME) is False
    text = path.read_text(encoding="utf-8")
    assert text.count("## Actual cost (actual)") == 1
    assert "Actual (actual): 2.000" in text and "(actual): 0.500" in text


def test_settle_waits_while_turn_has_no_cost(tmp_path):
    project = make_project(tmp_path)
    path = autopsy.write_autopsy(project, stop_entry(), make_session(tmp_path), {}, home=HOME)
    before = path.read_text(encoding="utf-8")
    set_pending(project, path)
    kiro_home = make_kiro_home_session(tmp_path, priced=False)
    assert autopsy.settle_autopsy(project, kiro_home=kiro_home, home=HOME) is False
    assert path.read_text(encoding="utf-8") == before
    assert read_pending(project)["turn_id"] == "t1"


def test_settle_without_pending_does_nothing(tmp_path):
    project = make_project(tmp_path)
    assert autopsy.settle_autopsy(project, kiro_home=tmp_path / "kh", home=HOME) is False
    assert not (project / ".kiro/tombstone/state.json").exists()


def test_watcher_loop_settles_autopsy(tmp_path):
    from tombstone import watcher
    project = make_project(tmp_path)
    path = autopsy.write_autopsy(project, stop_entry(), make_session(tmp_path), {}, home=HOME)
    set_pending(project, path)
    kiro_home = make_kiro_home_session(tmp_path, priced=True)
    watcher.run(project, kiro_home=kiro_home, max_polls=1, sleep=lambda s: None,
                dispatch=lambda *a: None, kill_step=lambda *a, **k: None)
    assert "## Actual cost (actual)" in path.read_text(encoding="utf-8")
    assert read_pending(project) is None


def _refusal_setup(tmp_path):
    project = make_project(tmp_path)
    good = autopsy.write_autopsy(project, stop_entry(), make_session(tmp_path), {}, home=HOME)
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    return project, good.parent, outside, make_kiro_home_session(tmp_path, priced=True)


def _assert_refused(project, kiro_home, victim, raw):
    before = victim.read_text(encoding="utf-8")
    set_pending(project, raw)
    assert autopsy.settle_autopsy(project, kiro_home=kiro_home, home=HOME) is False
    assert victim.read_text(encoding="utf-8") == before
    assert read_pending(project) is None
    log = (project / ".kiro/tombstone/errors.log").read_text(encoding="utf-8")
    assert log.count("autopsy append refused") == 1


def test_settle_refuses_parent_traversal(tmp_path):
    project, adir, outside, kh = _refusal_setup(tmp_path)
    _assert_refused(project, kh, outside, adir / ".." / ".." / ".." / ".." / "outside.md")


def test_settle_refuses_absolute_path_elsewhere(tmp_path):
    project, adir, outside, kh = _refusal_setup(tmp_path)
    _assert_refused(project, kh, outside, outside)


def test_settle_refuses_symlink_inside_autopsy_dir(tmp_path):
    project, adir, outside, kh = _refusal_setup(tmp_path)
    link = adir / "2026-11-06_evil.md"
    link.symlink_to(outside)
    _assert_refused(project, kh, outside, link)


def test_settle_refuses_non_md_file(tmp_path):
    project, adir, outside, kh = _refusal_setup(tmp_path)
    other = adir / "2026-11-06_notes.txt"
    other.write_text("keep\n", encoding="utf-8")
    _assert_refused(project, kh, other, other)


def test_settle_refuses_symlinked_autopsy_dir(tmp_path):
    project, adir, outside, kh = _refusal_setup(tmp_path)
    real = tmp_path / "elsewhere"
    real.mkdir()
    victim = real / "2026-11-06_x.md"
    victim.write_text("keep\n", encoding="utf-8")
    moved = adir.with_name("autopsy_real")
    adir.rename(moved)
    adir.symlink_to(real)
    _assert_refused(project, kh, victim, adir / victim.name)


def test_autopsy_quiet_minutes_one_decimal_under_ten():
    from tombstone import autopsy
    assert autopsy._fmt_minutes(0.4) == "0.4"
    assert autopsy._fmt_minutes(2.5) == "2.5"
    assert autopsy._fmt_minutes(12.5) == "12"
    assert autopsy._fmt_minutes(None) == "unknown"
