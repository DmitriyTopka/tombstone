"""Gap-filling tests for task 11.1: unpriced reasons, open vs closed request
counting, split task resolution and the plan balance gate message."""

from __future__ import annotations

import json
from pathlib import Path

from tombstone.caps import Config, check_task_start
from tombstone.sessions import parse_session
from tombstone.tombstones import TaskRef, resolve_task, resolve_tasks


def _v3_session(root: Path, events: list[dict]) -> Path:
    root.mkdir(parents=True)
    (root / "session.json").write_text(
        json.dumps({"id": root.name, "modelId": "auto"}), encoding="utf-8")
    lines = [json.dumps({"id": "x", "timestamp": ts, "payload": payload})
             for ts, payload in events]
    (root / "messages.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def _v2_session(path: Path, metering: object) -> Path:
    data = {
        "session_id": "s2",
        "cwd": "/work",
        "session_state": {"conversation_metadata": {"user_turn_metadatas": [{
            "model": "auto",
            "total_request_count": 2,
            "metering_usage": metering,
            "end_timestamp": "2026-11-05T10:00:10Z",
            "turn_duration": {"secs": 5, "nanos": 0},
        }]}},
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- every unpriced reason --------------------------------------------------

def test_v3_summary_without_prompt_turn_summaries_is_unpriced(tmp_path):
    root = _v3_session(tmp_path / "sess_a", [
        ("2026-11-05T10:00:00.000Z", {"type": "turn_start", "turnId": "t1"}),
        ("2026-11-05T10:00:05.000Z",
         {"type": "usage_summary", "turnId": "t1", "requestIds": ["a", "b"]}),
    ])
    turn = parse_session(root).turns[0]
    assert turn.state == "closed" and turn.cost is None
    assert turn.unpriced_reason == "no credit entry in usage_summary"
    assert turn.requests == 2


def test_v3_summary_with_only_non_credit_units_is_unpriced(tmp_path):
    root = _v3_session(tmp_path / "sess_b", [
        ("2026-11-05T10:00:00.000Z", {"type": "turn_start", "turnId": "t1"}),
        ("2026-11-05T10:00:05.000Z", {
            "type": "usage_summary", "turnId": "t1", "requestIds": ["a"],
            "promptTurnSummaries": [{"unit": "token", "usage": 120}]}),
    ])
    turn = parse_session(root).turns[0]
    assert turn.cost is None
    assert turn.unpriced_reason == "no credit entry in usage_summary"


def test_v2_metering_without_credit_unit_is_unpriced(tmp_path):
    path = _v2_session(tmp_path / "a.json", [{"unit": "token", "value": 9}])
    turn = parse_session(path).turns[0]
    assert turn.cost is None
    assert turn.unpriced_reason == "no credit entry in metering_usage"
    assert turn.requests == 2


def test_v2_metering_not_a_list_is_unpriced(tmp_path):
    path = _v2_session(tmp_path / "b.json", None)
    turn = parse_session(path).turns[0]
    assert turn.cost is None
    assert turn.unpriced_reason == "empty metering_usage"


# --- request counting: open against closed ---------------------------------

def test_closed_turn_counts_request_ids_open_turn_counts_assistant_events(tmp_path):
    root = _v3_session(tmp_path / "sess_c", [
        ("2026-11-05T10:00:00.000Z", {"type": "turn_start", "turnId": "t1"}),
        ("2026-11-05T10:00:01.000Z", {"type": "assistant", "turnId": "t1"}),
        ("2026-11-05T10:00:02.000Z", {"type": "assistant", "turnId": "t1"}),
        ("2026-11-05T10:00:03.000Z", {"type": "assistant", "turnId": "t1"}),
        ("2026-11-05T10:00:04.000Z", {
            "type": "usage_summary", "turnId": "t1", "requestIds": ["a", "b"],
            "promptTurnSummaries": [{"unit": "credit", "usage": 0.5}]}),
        ("2026-11-05T10:01:00.000Z", {"type": "turn_start", "turnId": "t2"}),
        ("2026-11-05T10:01:01.000Z", {"type": "assistant", "turnId": "t2"}),
        ("2026-11-05T10:01:02.000Z", {"type": "assistant", "turnId": "t2"}),
        ("2026-11-05T10:01:03.000Z", {"type": "assistant", "turnId": "t2"}),
        ("2026-11-05T10:01:04.000Z", {"type": "assistant", "turnId": "t2"}),
    ])
    closed, opened = parse_session(root).turns
    # Closed: the authoritative requestIds, not the 3 assistant events.
    assert closed.state == "closed" and closed.requests == 2
    assert closed.cost == 0.5
    # Open: assistant events so far, no cost, no end.
    assert opened.state == "open" and opened.requests == 4
    assert opened.cost is None and opened.end is None


# --- task id resolution: split and prompt naming ---------------------------

def _project(tmp_path: Path) -> Path:
    specs = tmp_path / ".kiro" / "specs" / "demo"
    specs.mkdir(parents=True)
    (specs / "tasks.md").write_text(
        "- [x] 1. First\n- [x] 2. Second\n- [ ] 3. Third\n", encoding="utf-8")
    return tmp_path


def test_resolve_tasks_returns_one_ref_per_checked_id_in_natural_order(tmp_path):
    project = _project(tmp_path)
    state = {"last_tasks_spec": "demo", "turn": {"checked": ["2", "1"]}}
    refs = resolve_tasks(project, {}, state)
    assert [ref.task_id for ref in refs] == ["1", "2"]
    assert all(isinstance(ref, TaskRef) for ref in refs)
    assert all(ref.source == "checkbox-diff" for ref in refs)
    assert resolve_task(project, {}, state).task_id == "1"


def test_resolve_tasks_stdin_wins_over_checked(tmp_path):
    project = _project(tmp_path)
    state = {"last_tasks_spec": "demo", "turn": {"checked": ["1", "2"]}}
    refs = resolve_tasks(project, {"taskId": "3"}, state)
    assert [ref.task_id for ref in refs] == ["3"]
    assert refs[0].source == "stdin"


def test_resolve_tasks_unknown_is_named_after_the_prompt(tmp_path):
    project = _project(tmp_path)
    state = {"last_tasks_spec": "demo",
             "turn": {"turn_id": "t9", "prompt_name": "Fix the writer"}}
    refs = resolve_tasks(project, {}, state)
    assert len(refs) == 1
    assert refs[0].task_id == "unknown"
    assert refs[0].name == "Fix the writer"
    assert refs[0].source == "prompt"
    assert refs[0].turn_id == "t9"


# --- gate message text for the plan balance --------------------------------

def test_plan_balance_block_message_text():
    task = TaskRef(spec="demo", task_id="4.2", name="Add stall evaluation",
                   source="test")
    decision = check_task_start(
        Config(task_cap=40, spec_cap=300, min_plan_balance=5.0),
        [], {}, task, balance=2.0)
    assert not decision.allow
    assert decision.message.startswith(
        'TOMBSTONE: task 4.2 "Add stall evaluation" blocked.')
    assert "Plan balance 2.000 (actual) is below budget.min_plan_balance 5.000." \
        in decision.message
    assert "Lower budget.min_plan_balance in tombstone.toml to continue." \
        in decision.message
    assert "\n" not in decision.message


def test_spec_cap_message_names_the_advice():
    task = TaskRef(spec="demo", task_id="1", name="Thing", source="test")
    record = {"id": "demo/0", "task_id": "0", "name": "Other", "spec": "demo",
              "outcome": "finished", "estimate": 300.0, "actual": 300.0}
    decision = check_task_start(Config(task_cap=1, spec_cap=300), [record], {}, task)
    assert "Spec cap reached (300.000 actual spent)." in decision.message
    assert "Raise caps.spec in tombstone.toml to continue." in decision.message
    assert 'Spec "demo" has 0.000 of 300.000 left (actual).' in decision.message
