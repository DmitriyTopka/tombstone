"""Stop diff: a box flipped by a shell command is still attributed (task 6.2 bug)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_tombstones_record import _project, _write_v3_session
from tombstone.tombstones import (
    TOMBSTONE_DIR,
    load_tombstones,
    on_turn_end,
    record_prompt,
    sync_checked_at_stop,
    update_json,
)

TASKS_BEFORE = "- [ ] 6.2 Evaluate\n- [ ] 7.1 Build\n"
TASKS_AFTER_62 = "- [x] 6.2 Evaluate\n- [ ] 7.1 Build\n"
TASKS_AFTER_71 = "- [x] 6.2 Evaluate\n- [x] 7.1 Build\n"

T1 = {"turn_id": "t1", "requests": 4, "cost": 0.4,
      "start": "2026-11-05T09:00:00Z", "end": "2026-11-05T09:05:00Z"}
T2 = {"turn_id": "t2", "requests": 6, "cost": 0.6,
      "start": "2026-11-05T10:00:00Z", "end": "2026-11-05T10:05:00Z"}


def _setup(tmp_path: Path):
    project = _project(tmp_path)
    tasks = project / ".kiro" / "specs" / "demo" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text(TASKS_BEFORE, encoding="utf-8")
    state_path = project / TOMBSTONE_DIR / "state.json"
    # A snapshot from earlier post-tool activity (an earlier tasks.md edit).
    state_path.write_text(json.dumps({
        "version": 1,
        "tasks_snapshot": {"demo": {"6.2": False, "7.1": False}},
        "last_tasks_spec": "demo",
    }), encoding="utf-8")
    return project, tasks, state_path


def test_shell_flip_gives_one_record_share_one_and_no_split_next_turn(tmp_path):
    project, tasks, state_path = _setup(tmp_path)
    kiro_home = tmp_path / "kiro_home"

    # Turn 1: the box is flipped by a shell command, no post-tool event.
    sid = _write_v3_session(kiro_home, project, turns=[T1])
    tasks.write_text(TASKS_AFTER_62, encoding="utf-8")
    record = on_turn_end(project, {"session_id": sid}, kiro_home=kiro_home)
    assert record is not None
    assert record["task_id"] == "6.2"
    assert record["turns"][0]["share"] == 1.0

    # Turn 2: a new prompt resets the list, another task is checked.
    update_json(state_path, lambda s: record_prompt(s, {"prompt": "next"}),
                default={"version": 1})
    kiro_home = tmp_path / "kiro_home2"
    _write_v3_session(kiro_home, project, turns=[T1, T2])
    tasks.write_text(TASKS_AFTER_71, encoding="utf-8")
    second = on_turn_end(project, {"session_id": sid}, kiro_home=kiro_home)
    assert second is not None
    assert second["task_id"] == "7.1"
    assert second["turns"][0]["share"] == 1.0

    store = load_tombstones(project / "tombstones.json")
    ids = sorted(item["task_id"] for item in store["tombstones"])
    assert ids == ["6.2", "7.1"]


def test_sync_never_counts_an_id_twice(tmp_path):
    project, tasks, _ = _setup(tmp_path)
    tasks.write_text(TASKS_AFTER_62, encoding="utf-8")
    state = {
        "tasks_snapshot": {"demo": {"6.2": False, "7.1": False}},
        "turn": {"checked": ["6.2"]},
    }
    sync_checked_at_stop(state, project)
    assert state["turn"]["checked"] == ["6.2"]
    # The snapshot is refreshed, so a second call adds nothing.
    sync_checked_at_stop(state, project)
    assert state["turn"]["checked"] == ["6.2"]
    assert state["tasks_snapshot"]["demo"]["6.2"] is True
