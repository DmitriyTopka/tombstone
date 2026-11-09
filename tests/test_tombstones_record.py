"""Tests for record building, the price window and the Stop hook (task 3.3)."""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

from tombstone import cli
from tombstone.sessions import Turn
from tombstone.tombstones import (
    DEFAULT_AVG_REQUEST_PRICE,
    TOMBSTONE_DIR,
    TaskRef,
    build_record,
    load_tombstones,
    on_turn_end,
    turn_entry,
    update_price_window,
)


def _closed_turn(
    *,
    turn_id: str = "t1",
    requests: int = 5,
    cost=0.39,
    model: str = "auto",
    start: str = "2026-11-05T09:12:03Z",
    end: str = "2026-11-05T09:18:27Z",
    unpriced_reason=None,
) -> Turn:
    return Turn(
        engine="v3",
        session_id="sess_demo",
        turn_id=turn_id,
        model=model,
        requests=requests,
        cost=cost,
        start=datetime.fromisoformat(start.replace("Z", "+00:00")),
        end=datetime.fromisoformat(end.replace("Z", "+00:00")),
        state="closed",
        unpriced_reason=unpriced_reason,
    )


def _ref(task_id: str = "4.2", spec: str = "tombstone", name="Add stall") -> TaskRef:
    return TaskRef(spec=spec, task_id=task_id, name=name, source="test")


# --- turn_entry -------------------------------------------------------------

def test_turn_entry_fields_and_minutes():
    turn = _closed_turn()
    entry = turn_entry(turn, estimate=0.225, share=1.0)
    assert entry["turn_id"] == "t1"
    assert entry["requests"] == 5
    assert entry["estimate"] == 0.225
    assert entry["actual"] == 0.39
    assert entry["share"] == 1.0
    # 09:12:03 to 09:18:27 is 6 minutes 24 seconds.
    assert entry["minutes"] == 6.4
    assert entry["start"] == "2026-11-05T09:12:03Z"
    assert entry["end"] == "2026-11-05T09:18:27Z"
    assert entry["unpriced_reason"] is None


def test_turn_entry_unpriced_actual_is_none():
    turn = _closed_turn(cost=None, unpriced_reason="no credit entry in usage_summary")
    entry = turn_entry(turn, estimate=0.225, share=1.0)
    assert entry["actual"] is None
    assert entry["unpriced_reason"] == "no credit entry in usage_summary"
    # The estimate is still carried.
    assert entry["estimate"] == 0.225


# --- build_record -----------------------------------------------------------

def test_build_record_single_turn():
    record = build_record(
        _ref(),
        _closed_turn(),
        engine="v3",
        model="auto",
        estimate=0.225,
        outcome="finished",
    )
    assert record["id"] == "tombstone/4.2"
    assert record["task_id"] == "4.2"
    assert record["spec"] == "tombstone"
    assert record["engine"] == "v3"
    assert record["model"] == "auto"
    assert record["requests"] == 5
    assert record["actual"] == 0.39
    assert record["estimate"] == 0.225
    assert record["outcome"] == "finished"
    assert record["false_positive"] is None
    assert record["date"] is not None
    assert len(record["turns"]) == 1


def test_build_record_unknown_task_uses_turn_id_in_id():
    ref = TaskRef(spec=None, task_id="unknown", name=None, source="fallback",
                  turn_id="t-123")
    record = build_record(ref, _closed_turn(turn_id="t-123"), engine="v3",
                          model="auto", estimate=None)
    assert record["id"] == "unknown/t-123"


def test_build_record_actual_null_while_any_turn_unpriced():
    # One priced turn and one unpriced turn fold into the same record. While
    # any turn is unpriced the top-level actual stays null (R4.4).
    priced = _closed_turn(turn_id="t1", requests=5, cost=0.39)
    unpriced = _closed_turn(turn_id="t2", requests=4, cost=None,
                            unpriced_reason="no credit entry in usage_summary")
    first = build_record(_ref(), priced, engine="v3", model="auto", estimate=0.225)
    second = build_record(_ref(), unpriced, engine="v3", model="auto",
                          estimate=0.18, existing=first)
    assert len(second["turns"]) == 2
    assert second["actual"] is None
    assert second["requests"] == 9


def test_build_record_share_weights_sums():
    # Two tasks checked in one turn: share 0.5 each. The record for one of them
    # carries half the requests-driven estimate and half the actual.
    turn = _closed_turn(requests=10, cost=0.50)
    record = build_record(_ref(), turn, engine="v3", model="auto",
                          estimate=0.45, share=0.5)
    assert record["turns"][0]["share"] == 0.5
    assert record["actual"] == 0.25
    assert record["estimate"] == 0.225


def test_build_record_preserves_existing_false_positive():
    turn = _closed_turn()
    existing = {"id": "tombstone/4.2", "false_positive": True, "turns": []}
    record = build_record(_ref(), turn, engine="v3", model="auto",
                          estimate=0.2, existing=existing)
    assert record["false_positive"] is True


# --- update_price_window ----------------------------------------------------

def test_price_window_average():
    state = {}
    update_price_window(state, 14, 0.71)
    update_price_window(state, 9, 0.357)
    assert state["price_window"] == [[14, 0.71], [9, 0.357]]
    # avg = (0.71 + 0.357) / (14 + 9)
    assert round(state["avg_request_price"], 6) == round(1.067 / 23, 6)


def test_price_window_drops_oldest_beyond_fifty_requests():
    state = {}
    # Three turns of 30 requests each. After the third, dropping the first
    # still leaves 60 requests (>= 50), so the oldest is dropped.
    update_price_window(state, 30, 1.0)
    update_price_window(state, 30, 1.0)
    update_price_window(state, 30, 1.0)
    total = sum(item[0] for item in state["price_window"])
    assert total == 60
    assert len(state["price_window"]) == 2


def test_price_window_keeps_window_when_dropping_would_go_below_fifty():
    state = {}
    update_price_window(state, 30, 1.0)
    update_price_window(state, 30, 1.0)
    # Two turns total 60. Dropping the first leaves 30 (< 50), so both stay.
    assert len(state["price_window"]) == 2


def test_price_window_ignores_unpriced_or_zero_request_turns():
    state = {}
    update_price_window(state, None, 0.5)
    update_price_window(state, 0, 0.5)
    update_price_window(state, 5, None)
    assert "price_window" not in state


# --- on_turn_end against a V3 session on disk -------------------------------


def _write_v3_session(kiro_home: Path, project: Path, *, turns: list[dict]) -> str:
    """Write a minimal V3 session and index so find_session can locate it.

    Each turn dict is ``{"turn_id", "requests", "cost"|None, "start", "end"}``.
    Returns the session id.
    """
    project_hash = "testhash"
    session_id = "sess_rec-0001"
    sess_dir = kiro_home / "sessions" / project_hash / session_id
    sess_dir.mkdir(parents=True)

    (sess_dir / "session.json").write_text(json.dumps({
        "id": session_id,
        "modelId": "auto",
        "workspacePaths": [str(project)],
    }), encoding="utf-8")

    lines: list[str] = []
    for turn in turns:
        lines.append(json.dumps({
            "id": f"start-{turn['turn_id']}",
            "timestamp": turn["start"],
            "payload": {"type": "turn_start", "turnId": turn["turn_id"]},
        }))
        for i in range(turn["requests"]):
            lines.append(json.dumps({
                "id": f"a-{turn['turn_id']}-{i}",
                "timestamp": turn["start"],
                "payload": {"type": "assistant", "turnId": turn["turn_id"]},
            }))
        summaries = []
        if turn["cost"] is not None:
            summaries.append({"unit": "credit", "usage": turn["cost"]})
        lines.append(json.dumps({
            "id": f"end-{turn['turn_id']}",
            "timestamp": turn["end"],
            "payload": {
                "type": "usage_summary",
                "turnId": turn["turn_id"],
                "promptTurnSummaries": summaries,
                "requestIds": [f"r{i}" for i in range(turn["requests"])],
            },
        }))
    (sess_dir / "messages.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    index_dir = kiro_home / "session-index"
    index_dir.mkdir(parents=True)
    (index_dir / f"{project_hash}.jsonl").write_text(json.dumps({
        "op": "create",
        "sessionPath": str(sess_dir),
        "at": "2026-11-05T09:20:00Z",
    }) + "\n", encoding="utf-8")

    return session_id


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".kiro").mkdir(exist_ok=True)
    (tmp_path / ".tombstone").mkdir(parents=True)
    return tmp_path


def test_on_turn_end_writes_record_for_closed_turn(tmp_path):
    project = _project(tmp_path)
    kiro_home = tmp_path / "kiro_home"
    # No spec: the task resolves to unknown, so the record id uses the turn id.
    (project / ".kiro" / "specs").mkdir(parents=True)
    sid = _write_v3_session(kiro_home, project, turns=[
        {"turn_id": "t1", "requests": 5, "cost": 0.39,
         "start": "2026-11-05T09:12:03Z", "end": "2026-11-05T09:18:27Z"},
    ])

    record = on_turn_end(project, {"session_id": sid}, kiro_home=kiro_home)
    assert record is not None
    assert record["requests"] == 5
    assert record["actual"] == 0.39
    # Estimate uses the default average price until the window has data.
    assert record["estimate"] == round(5 * DEFAULT_AVG_REQUEST_PRICE, 4)

    store = load_tombstones(project / "tombstones.json")
    assert len(store["tombstones"]) == 1
    assert store["tombstones"][0]["turns"][0]["turn_id"] == "t1"


def test_on_turn_end_is_idempotent(tmp_path):
    project = _project(tmp_path)
    kiro_home = tmp_path / "kiro_home"
    (project / ".kiro" / "specs").mkdir(parents=True)
    sid = _write_v3_session(kiro_home, project, turns=[
        {"turn_id": "t1", "requests": 5, "cost": 0.39,
         "start": "2026-11-05T09:12:03Z", "end": "2026-11-05T09:18:27Z"},
    ])

    on_turn_end(project, {"session_id": sid}, kiro_home=kiro_home)
    second = on_turn_end(project, {"session_id": sid}, kiro_home=kiro_home)
    # The only closed turn is already recorded, so the second call records
    # nothing new.
    assert second is None
    store = load_tombstones(project / "tombstones.json")
    assert len(store["tombstones"]) == 1
    assert len(store["tombstones"][0]["turns"]) == 1


def test_on_turn_end_records_newest_closed_turn(tmp_path):
    project = _project(tmp_path)
    kiro_home = tmp_path / "kiro_home"
    (project / ".kiro" / "specs").mkdir(parents=True)
    sid = _write_v3_session(kiro_home, project, turns=[
        {"turn_id": "t1", "requests": 3, "cost": 0.20,
         "start": "2026-11-05T09:00:00Z", "end": "2026-11-05T09:05:00Z"},
        {"turn_id": "t2", "requests": 6, "cost": 0.40,
         "start": "2026-11-05T09:10:00Z", "end": "2026-11-05T09:16:00Z"},
    ])

    record = on_turn_end(project, {"session_id": sid}, kiro_home=kiro_home)
    assert record is not None
    assert record["turns"][0]["turn_id"] == "t2"
    assert record["requests"] == 6


def test_on_turn_end_unpriced_turn_has_actual_null(tmp_path):
    project = _project(tmp_path)
    kiro_home = tmp_path / "kiro_home"
    (project / ".kiro" / "specs").mkdir(parents=True)
    sid = _write_v3_session(kiro_home, project, turns=[
        {"turn_id": "t1", "requests": 5, "cost": None,
         "start": "2026-11-05T09:12:03Z", "end": "2026-11-05T09:18:27Z"},
    ])

    record = on_turn_end(project, {"session_id": sid}, kiro_home=kiro_home)
    assert record is not None
    # The summary landed without a credit entry: actual is null (R4.2).
    assert record["actual"] is None
    # No price window is written for an unpriced turn.
    state_path = project / TOMBSTONE_DIR / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        assert "price_window" not in state


def test_on_turn_end_updates_price_window(tmp_path):
    project = _project(tmp_path)
    kiro_home = tmp_path / "kiro_home"
    (project / ".kiro" / "specs").mkdir(parents=True)
    sid = _write_v3_session(kiro_home, project, turns=[
        {"turn_id": "t1", "requests": 5, "cost": 0.39,
         "start": "2026-11-05T09:12:03Z", "end": "2026-11-05T09:18:27Z"},
    ])

    on_turn_end(project, {"session_id": sid}, kiro_home=kiro_home)
    state = json.loads((project / TOMBSTONE_DIR / "state.json").read_text())
    assert state["price_window"] == [[5, 0.39]]
    assert round(state["avg_request_price"], 6) == round(0.39 / 5, 6)


def test_on_turn_end_no_session_returns_none(tmp_path):
    project = _project(tmp_path)
    kiro_home = tmp_path / "kiro_home"
    kiro_home.mkdir()
    assert on_turn_end(project, {}, kiro_home=kiro_home) is None


def test_on_turn_end_resolves_task_from_stdin_id(tmp_path):
    project = _project(tmp_path)
    kiro_home = tmp_path / "kiro_home"
    specs = project / ".kiro" / "specs" / "tombstone"
    specs.mkdir(parents=True)
    (specs / "tasks.md").write_text(
        "- [ ] 3.3 Record building and the Stop hook\n", encoding="utf-8"
    )
    sid = _write_v3_session(kiro_home, project, turns=[
        {"turn_id": "t1", "requests": 5, "cost": 0.39,
         "start": "2026-11-05T09:12:03Z", "end": "2026-11-05T09:18:27Z"},
    ])

    record = on_turn_end(project, {"session_id": sid, "task_id": "3.3"},
                         kiro_home=kiro_home)
    assert record["id"] == "tombstone/3.3"
    assert record["task_id"] == "3.3"
    assert record["name"] == "Record building and the Stop hook"


# --- the stop hook end to end -----------------------------------------------

def test_stop_hook_fails_open_on_bad_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert cli.main(["hook", "stop"]) == 0


def test_stop_hook_calls_on_turn_end(tmp_path, monkeypatch):
    project = _project(tmp_path)
    calls = {}

    def fake_on_turn_end(proj, stdin, **kwargs):
        calls["project"] = proj
        calls["stdin"] = stdin
        return None

    from tombstone import tombstones
    monkeypatch.setattr(tombstones, "on_turn_end", fake_on_turn_end)

    stdin = {"cwd": str(project), "session_id": "sess_x"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin)))
    assert cli.main(["hook", "stop"]) == 0
    assert Path(calls["project"]) == project
    assert calls["stdin"]["session_id"] == "sess_x"


def test_stop_hook_exits_zero_when_on_turn_end_raises(tmp_path, monkeypatch):
    project = _project(tmp_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    from tombstone import tombstones
    monkeypatch.setattr(tombstones, "on_turn_end", boom)

    stdin = {"cwd": str(project), "session_id": "sess_x"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin)))
    # The hook catches the error, logs it and exits 0 (fail open).
    assert cli.main(["hook", "stop"]) == 0
    log = (project / TOMBSTONE_DIR / "errors.log").read_text()
    assert "kaboom" in log


# --- prompt hook and unknown work turns (R2.3, R2.4) -------------------------

PROMPT = "Bug fix in the tombstone writer (tasks 3.2 and 3.3 are already done)"
TASKS_TWO = "# Tasks\n\n- [ ] 4.1 First thing\n- [ ] 4.2 Second thing\n"


def _run_hook(argv, payload, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return cli.main(argv)


def _spec_project(tmp_path: Path) -> Path:
    project = _project(tmp_path)
    spec = project / ".kiro" / "specs" / "demo"
    spec.mkdir(parents=True)
    (spec / "tasks.md").write_text(TASKS_TWO, encoding="utf-8")
    return project


def test_prompt_name_takes_first_eight_words_and_strips_home():
    from tombstone.tombstones import prompt_name
    assert prompt_name(PROMPT) == "Bug fix in the tombstone writer (tasks 3.2"
    home = Path("/Users/alice")
    assert prompt_name("look at /Users/alice/proj/a.py now", home) == "look at ~/proj/a.py now"


def test_turn_without_checkbox_edit_is_one_unknown_record(tmp_path, monkeypatch):
    project = _spec_project(tmp_path)
    kiro_home = tmp_path / "kiro_home"
    sid = _write_v3_session(kiro_home, project, turns=[
        {"turn_id": "t1", "requests": 4, "cost": 0.3,
         "start": "2026-11-05T09:00:00Z", "end": "2026-11-05T09:05:00Z"},
    ])
    rc = _run_hook(["hook", "prompt"], {
        "session_id": sid, "hook_event_name": "UserPromptSubmit",
        "cwd": str(project), "prompt": PROMPT}, monkeypatch)
    assert rc == 0

    on_turn_end(project, {"session_id": sid}, kiro_home=kiro_home)
    store = load_tombstones(project / "tombstones.json")
    assert len(store["tombstones"]) == 1
    record = store["tombstones"][0]
    assert record["task_id"] == "unknown"
    assert record["name"] == "Bug fix in the tombstone writer (tasks 3.2"
    assert record["id"] == "unknown/t1"
    # The first unchecked task (4.1) did not get the cost.
    assert record["actual"] == 0.3


def test_turn_that_checks_a_box_ends_as_that_task(tmp_path, monkeypatch):
    project = _spec_project(tmp_path)
    kiro_home = tmp_path / "kiro_home"
    sid = _write_v3_session(kiro_home, project, turns=[
        {"turn_id": "t1", "requests": 4, "cost": 0.3,
         "start": "2026-11-05T09:00:00Z", "end": "2026-11-05T09:05:00Z"},
    ])
    _run_hook(["hook", "prompt"], {"session_id": sid, "cwd": str(project),
                                    "prompt": "Do task 4.2"}, monkeypatch)
    tasks_md = project / ".kiro" / "specs" / "demo" / "tasks.md"
    tasks_md.write_text(TASKS_TWO.replace("- [ ] 4.2", "- [x] 4.2"), encoding="utf-8")
    _run_hook(["hook", "post-tool"], {
        "session_id": sid, "cwd": str(project), "tool_name": "str_replace",
        "tool_input": {"path": str(tasks_md), "oldStr": "- [ ] 4.2 Second thing",
                       "newStr": "- [x] 4.2 Second thing"}}, monkeypatch)

    on_turn_end(project, {"session_id": sid}, kiro_home=kiro_home)
    store = load_tombstones(project / "tombstones.json")
    assert len(store["tombstones"]) == 1
    assert store["tombstones"][0]["task_id"] == "4.2"
    assert store["tombstones"][0]["name"] == "Second thing"


def test_two_boxes_in_one_turn_split_equally(tmp_path, monkeypatch):
    project = _spec_project(tmp_path)
    kiro_home = tmp_path / "kiro_home"
    sid = _write_v3_session(kiro_home, project, turns=[
        {"turn_id": "t1", "requests": 4, "cost": 0.4,
         "start": "2026-11-05T09:00:00Z", "end": "2026-11-05T09:05:00Z"},
    ])
    _run_hook(["hook", "prompt"], {"cwd": str(project), "prompt": "both"}, monkeypatch)
    from tombstone.tombstones import update_json
    update_json(project / ".tombstone" / "state.json",
                lambda s: s["turn"].update(checked=["4.1", "4.2"]) or s,
                default={"version": 1})
    on_turn_end(project, {"session_id": sid}, kiro_home=kiro_home)
    store = load_tombstones(project / "tombstones.json")
    assert sorted(r["task_id"] for r in store["tombstones"]) == ["4.1", "4.2"]
    assert all(r["turns"][0]["share"] == 0.5 for r in store["tombstones"])
    assert sum(r["actual"] for r in store["tombstones"]) == 0.4
