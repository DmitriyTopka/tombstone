"""Two bugs from the live run of task 4.2.

Bug A: a task id added to tasks.md already checked counted as checked now.
Bug B: reconcile filled one record of a split turn and left the other null.
"""

from __future__ import annotations

import json

from tombstone.tombstones import (
    TOMBSTONE_DIR,
    load_tombstones,
    reconcile_pending,
    reconcile_turn,
    refresh_snapshot,
)

from .test_reconcile_survival import (
    COST,
    REAL_REQUESTS,
    SESSION_ID,
    TURN_ID,
    _land_cost,
    _open_lines,
    _project,
    _session_dir,
)
from .test_tombstones_tasks import _spec

TASKS = """# Plan

- [x] 3.4 Added already checked
- [ ] 4.1 Open task
- [ ] 4.2 Another open task
"""


# --- Bug A -------------------------------------------------------------------


def test_new_id_added_already_checked_is_not_checked_now(tmp_path):
    _spec(tmp_path, "demo", TASKS)
    # The snapshot from before the turn does not contain 3.4.
    state = {"tasks_snapshot": {"demo": {"4.1": False, "4.2": False}},
             "turn": {"checked": []}}
    refresh_snapshot(state, tmp_path, "demo")
    assert state["turn"]["checked"] == []
    # It just enters the snapshot.
    assert state["tasks_snapshot"]["demo"]["3.4"] is True


def test_unchecked_in_snapshot_then_checked_still_counts(tmp_path):
    _spec(tmp_path, "demo", TASKS)
    state = {"tasks_snapshot": {"demo": {"3.4": False, "4.1": False}},
             "turn": {"checked": []}}
    refresh_snapshot(state, tmp_path, "demo")
    assert state["turn"]["checked"] == ["3.4"]


def test_edit_adding_a_checked_line_is_not_checked_now(tmp_path):
    _spec(tmp_path, "demo", TASKS)
    state = {"turn": {"checked": []}}
    stdin = {"tool_input": {
        "oldStr": "- [ ] 4.1 Open task\n",
        "newStr": "- [x] 3.4 Added already checked\n- [ ] 4.1 Open task\n",
    }}
    refresh_snapshot(state, tmp_path, "demo", stdin=stdin)
    assert state["turn"]["checked"] == []


def test_edit_flipping_unchecked_to_checked_still_counts(tmp_path):
    _spec(tmp_path, "demo", TASKS.replace("[ ] 4.1", "[x] 4.1"))
    state = {"turn": {"checked": []}}
    stdin = {"tool_input": {"oldStr": "- [ ] 4.1 Open task\n",
                            "newStr": "- [x] 4.1 Open task\n"}}
    refresh_snapshot(state, tmp_path, "demo", stdin=stdin)
    assert state["turn"]["checked"] == ["4.1"]


# --- Bug B -------------------------------------------------------------------


def _split_store(project) -> None:
    def entry(share):
        return {"session_id": SESSION_ID, "turn_id": TURN_ID, "requests": 23,
                "estimate": 4.0, "actual": None, "minutes": None,
                "start": None, "end": None, "share": share,
                "unpriced_reason": None}

    def record(task, share):
        return {"id": f"demo/{task}", "spec": "demo", "task": task,
                "turns": [entry(share)], "requests": 23, "estimate": 4.0 * share,
                "actual": None, "minutes": None, "date": None}

    store = {"version": 1, "tombstones": [record("3.4", 0.5),
                                          record("4.2", 0.5)]}
    (project / "tombstones.json").write_text(json.dumps(store),
                                             encoding="utf-8")


def _check_both_filled(project) -> None:
    records = load_tombstones(project / "tombstones.json")["tombstones"]
    assert len(records) == 2
    for record in records:
        assert record["turns"][0]["actual"] == COST
        assert record["turns"][0]["requests"] == REAL_REQUESTS
        assert record["turns"][0]["share"] == 0.5
        assert record["requests"] == REAL_REQUESTS
        assert record["actual"] == round(COST * 0.5, 4)


def _split_setup(tmp_path):
    project = _project(tmp_path)
    kiro_home = tmp_path / "kiro_home"
    sess_dir = _session_dir(kiro_home, project)
    (sess_dir / "messages.jsonl").write_text(
        "\n".join(_open_lines()) + "\n", encoding="utf-8")
    _split_store(project)
    _land_cost(sess_dir)
    return project, kiro_home


def test_reconcile_turn_fills_every_record_of_a_split_turn(tmp_path):
    project, kiro_home = _split_setup(tmp_path)
    reconcile_turn(project, SESSION_ID, TURN_ID, kiro_home=kiro_home,
                   sleep=lambda _s: None, clock=lambda: 0.0)
    _check_both_filled(project)


def test_safety_net_fills_every_record_of_a_split_turn(tmp_path):
    project, kiro_home = _split_setup(tmp_path)
    assert reconcile_pending(project, kiro_home=kiro_home) == 1
    _check_both_filled(project)
    assert (project / TOMBSTONE_DIR / "reconcile.log").exists()


def test_safety_net_fixes_the_record_a_partial_fill_left_null(tmp_path):
    project, kiro_home = _split_setup(tmp_path)
    store = load_tombstones(project / "tombstones.json")
    # Simulate the old bug: only the first record was filled.
    first = store["tombstones"][0]["turns"][0]
    first["actual"] = COST
    first["requests"] = REAL_REQUESTS
    store["tombstones"][0]["actual"] = round(COST * 0.5, 4)
    (project / "tombstones.json").write_text(json.dumps(store),
                                             encoding="utf-8")
    assert reconcile_pending(project, kiro_home=kiro_home) == 1
    _check_both_filled(project)
