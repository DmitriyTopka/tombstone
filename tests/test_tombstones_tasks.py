"""Tests for tasks.md parsing and task id resolution (task 3.2)."""

from __future__ import annotations

import json
from pathlib import Path

from tombstone import cli
from tombstone.tombstones import (
    TOMBSTONE_DIR,
    active_spec,
    parse_tasks,
    refresh_snapshot,
    resolve_task,
    stdin_tasks_spec,
    tasks_path_spec,
)

TASKS_MD = """# Implementation Plan

- [ ] 1. Session parser
  - One parser, two formats.
  - _Requirements: R1.1_

  - [x] 1.1 Fixtures and the V3 parser
    - Copy the session.
  - [ ] 1.2 The V2 parser
    - Add the branch.

- [-] 2. CLI entry point
  - argparse and lazy imports.

- [ ] 3. Tombstone writer
  - [x] 3.1 Locked JSON store
  - [ ] 3.2 tasks.md parsing and task id resolution
"""


def _spec(project: Path, name: str, text: str) -> Path:
    specs = project / ".kiro" / "specs" / name
    specs.mkdir(parents=True, exist_ok=True)
    path = specs / "tasks.md"
    path.write_text(text, encoding="utf-8")
    return path


def _sidecar(project: Path) -> Path:
    directory = project / TOMBSTONE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


# --- parse_tasks -----------------------------------------------------------

def test_parse_tasks_reads_ids_names_and_checked(tmp_path):
    path = _spec(tmp_path, "demo", TASKS_MD)
    tasks = parse_tasks(path)
    by_id = {t.task_id: t for t in tasks}

    assert by_id["1"].name == "Session parser"
    assert by_id["1"].checked is False
    assert by_id["1.1"].name == "Fixtures and the V3 parser"
    assert by_id["1.1"].checked is True
    assert by_id["1.2"].checked is False
    # A "[-]" is not a completed task.
    assert by_id["2"].checked is False
    assert by_id["3.1"].checked is True
    assert by_id["3.2"].checked is False
    # Every parsed task carries the spec folder name.
    assert all(t.spec == "demo" for t in tasks)


def test_parse_tasks_missing_file_is_empty(tmp_path):
    assert parse_tasks(tmp_path / "nope" / "tasks.md") == []


def test_parse_tasks_numberless_checkbox_gets_line_id(tmp_path):
    text = "- [ ] a feature bullet with no number\n- [x] 1. Real task\n"
    path = _spec(tmp_path, "demo", text)
    tasks = parse_tasks(path)
    ids = [t.task_id for t in tasks]
    assert ids == ["L1", "1"]
    assert tasks[0].name == "a feature bullet with no number"


# --- active_spec -----------------------------------------------------------

def test_active_spec_prefers_last_touched(tmp_path):
    _spec(tmp_path, "alpha", "- [ ] 1. a\n")
    _spec(tmp_path, "beta", "- [ ] 1. b\n")
    state = {"last_tasks_spec": "beta"}
    assert active_spec(tmp_path, state) == "beta"


def test_active_spec_newest_with_unchecked(tmp_path):
    older = _spec(tmp_path, "alpha", "- [ ] 1. a\n")
    newer = _spec(tmp_path, "beta", "- [ ] 1. b\n")
    import os

    os.utime(older, (1_000, 1_000))
    os.utime(newer, (2_000, 2_000))
    assert active_spec(tmp_path, {}) == "beta"


def test_active_spec_skips_fully_checked(tmp_path):
    done = _spec(tmp_path, "alpha", "- [x] 1. a\n")
    todo = _spec(tmp_path, "beta", "- [ ] 1. b\n")
    import os

    # Make the fully checked spec the newest; it must still be skipped.
    os.utime(done, (9_000, 9_000))
    os.utime(todo, (1_000, 1_000))
    assert active_spec(tmp_path, {}) == "beta"


def test_active_spec_single_spec(tmp_path):
    _spec(tmp_path, "only", "- [x] 1. a\n")
    assert active_spec(tmp_path, {}) == "only"


def test_active_spec_none_when_no_specs(tmp_path):
    (tmp_path / ".kiro").mkdir()
    assert active_spec(tmp_path, {}) is None


# --- resolve_task: the four steps ------------------------------------------

def test_resolve_task_step1_stdin(tmp_path):
    _spec(tmp_path, "demo", TASKS_MD)
    ref = resolve_task(tmp_path, {"task_id": "3.2"}, {"last_tasks_spec": "demo"})
    assert ref.task_id == "3.2"
    assert ref.source == "stdin"
    assert ref.name == "tasks.md parsing and task id resolution"


def test_resolve_task_step1_nested_task_id(tmp_path):
    _spec(tmp_path, "demo", TASKS_MD)
    ref = resolve_task(tmp_path, {"task": {"id": "1.1"}}, {"last_tasks_spec": "demo"})
    assert ref.task_id == "1.1"
    assert ref.source == "stdin"


def test_resolve_task_step2_checkbox_diff(tmp_path):
    _spec(tmp_path, "demo", TASKS_MD)
    state = {"last_tasks_spec": "demo", "turn": {"checked": ["3.2"]}}
    ref = resolve_task(tmp_path, {}, state)
    assert ref.task_id == "3.2"
    assert ref.source == "checkbox-diff"


def test_resolve_task_step2_multiple_checked_takes_first(tmp_path):
    _spec(tmp_path, "demo", TASKS_MD)
    state = {"last_tasks_spec": "demo", "turn": {"checked": ["3.2", "1.2"]}}
    ref = resolve_task(tmp_path, {}, state)
    assert ref.task_id == "1.2"
    assert ref.source == "checkbox-diff"


def test_resolve_task_never_guesses_first_unchecked(tmp_path):
    _spec(tmp_path, "demo", TASKS_MD)
    state = {"last_tasks_spec": "demo", "turn": {"prompt_name": "Fix the writer"}}
    ref = resolve_task(tmp_path, {}, state)
    assert ref.task_id == "unknown"
    assert ref.name == "Fix the writer"


def test_resolve_task_step4_unknown_with_turn_id(tmp_path):
    (tmp_path / ".kiro").mkdir()
    state = {"turn": {"turn_id": "t-123"}}
    ref = resolve_task(tmp_path, {}, state)
    assert ref.task_id == "unknown"
    assert ref.source == "fallback"
    assert ref.turn_id == "t-123"


# --- path matching ---------------------------------------------------------

def test_tasks_path_spec_matches_only_tasks_md():
    assert tasks_path_spec("/home/u/proj/.kiro/specs/tombstone/tasks.md") == "tombstone"
    assert tasks_path_spec("C:\\proj\\.kiro\\specs\\demo\\tasks.md") == "demo"
    assert tasks_path_spec("/home/u/proj/.kiro/specs/tombstone/design.md") is None
    assert tasks_path_spec("/home/u/proj/src/tasks.md") is None
    assert tasks_path_spec(None) is None


def test_stdin_tasks_spec_finds_nested_path():
    stdin = {"tool_input": {"path": "/x/.kiro/specs/alpha/tasks.md"}}
    assert stdin_tasks_spec(stdin) == "alpha"
    assert stdin_tasks_spec({"tool_input": {"path": "/x/src/main.py"}}) is None


# --- refresh_snapshot ------------------------------------------------------

def test_refresh_snapshot_records_newly_checked(tmp_path):
    _spec(tmp_path, "demo", TASKS_MD)
    # In the file, 3.1 is checked. The prior snapshot says it was unchecked,
    # so refreshing records it as newly checked this turn. 1.1 (also checked
    # in the file) was already checked in the snapshot, so it is not recorded.
    state = {
        "tasks_snapshot": {"demo": {"3.1": False, "1.1": True}},
        "turn": {"checked": []},
    }
    refresh_snapshot(state, tmp_path, "demo")
    assert state["turn"]["checked"] == ["3.1"]
    # The snapshot is refreshed to the current file state.
    assert state["tasks_snapshot"]["demo"]["3.1"] is True
    assert state["tasks_snapshot"]["demo"]["3.2"] is False
    assert state["last_tasks_spec"] == "demo"


def test_refresh_snapshot_no_duplicate_ids(tmp_path):
    _spec(tmp_path, "demo", TASKS_MD)
    state = {
        "tasks_snapshot": {"demo": {"3.1": False, "1.1": True}},
        "turn": {"checked": ["3.1"]},
    }
    refresh_snapshot(state, tmp_path, "demo")
    # 3.1 was already in the list; it is not added twice.
    assert state["turn"]["checked"] == ["3.1"]


def test_refresh_snapshot_first_run_no_edit_checks_nothing(tmp_path):
    # Bug 2: no prior snapshot and no edit diff means nothing is newly checked,
    # even though 1.1 and 3.1 are already [x] in the file.
    _spec(tmp_path, "demo", TASKS_MD)
    state = {"turn": {"checked": []}}
    refresh_snapshot(state, tmp_path, "demo")
    assert state["turn"]["checked"] == []
    # The snapshot still lands.
    assert state["tasks_snapshot"]["demo"]["3.1"] is True


# TASKS_MD with 3.2 already flipped to [x], as the file looks AFTER the edit
# that the post-tool hook sees (the hook runs after the tool writes the file).
TASKS_MD_32_DONE = TASKS_MD.replace(
    "  - [ ] 3.2 tasks.md parsing and task id resolution",
    "  - [x] 3.2 tasks.md parsing and task id resolution",
)


def test_refresh_snapshot_first_run_reads_str_replace_diff(tmp_path):
    # Bug 2: the first run reads the checked task from the edit's oldStr/newStr.
    # The file (post-edit) has 1.1, 3.1 and 3.2 [x], but only 3.2 flipped in
    # this edit, so only 3.2 counts as checked this turn.
    _spec(tmp_path, "demo", TASKS_MD_32_DONE)
    state = {"turn": {"checked": []}}
    stdin = {
        "tool_input": {
            "oldStr": "  - [ ] 3.2 tasks.md parsing and task id resolution\n",
            "newStr": "  - [x] 3.2 tasks.md parsing and task id resolution\n",
        }
    }
    refresh_snapshot(state, tmp_path, "demo", stdin=stdin)
    assert state["turn"]["checked"] == ["3.2"]


def test_refresh_snapshot_first_run_ignores_edit_for_unchanged_file_task(tmp_path):
    # Bug 2: the edit only counts a task that is actually [x] in the file now.
    # Here the edit claims to check task 9 which does not exist in the file.
    _spec(tmp_path, "demo", TASKS_MD)
    state = {"turn": {"checked": []}}
    stdin = {"tool_input": {"oldStr": "- [ ] 9. ghost\n",
                            "newStr": "- [x] 9. ghost\n"}}
    refresh_snapshot(state, tmp_path, "demo", stdin=stdin)
    assert state["turn"]["checked"] == []


def test_refresh_snapshot_resets_checked_on_new_turn(tmp_path):
    # Bug 2: a new turn id clears the carried-over checked list first. The
    # snapshot already matches the file, so the only id recorded is the one
    # flipped in this turn (none here), not the leftover 3.1 from turn-A.
    _spec(tmp_path, "demo", TASKS_MD)
    state = {
        "tasks_snapshot": {"demo": {t.task_id: t.checked
                                    for t in parse_tasks(
                                        tmp_path / ".kiro" / "specs" / "demo"
                                        / "tasks.md")}},
        "turn": {"turn_id": "turn-A", "checked": ["3.1"]},
    }
    refresh_snapshot(state, tmp_path, "demo", turn_id="turn-B")
    assert state["turn"]["turn_id"] == "turn-B"
    assert state["turn"]["checked"] == []


# --- the post-tool hook end to end -----------------------------------------

def _run_hook(monkeypatch, stdin: dict) -> int:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin)))
    return cli.main(["hook", "post-tool"])


def test_post_tool_hook_records_checked_id(tmp_path, monkeypatch):
    _sidecar(tmp_path)
    tasks_path = _spec(tmp_path, "demo", TASKS_MD)
    state_path = tmp_path / TOMBSTONE_DIR / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks_snapshot": {"demo": {"3.1": False}},
                "turn": {"checked": []},
            }
        )
    )

    stdin = {"cwd": str(tmp_path), "tool_input": {"path": str(tasks_path)}}
    assert _run_hook(monkeypatch, stdin) == 0

    state = json.loads(state_path.read_text())
    assert "3.1" in state["turn"]["checked"]
    assert state["last_tasks_spec"] == "demo"


def test_post_tool_hook_ignores_non_tasks_path(tmp_path, monkeypatch):
    _sidecar(tmp_path)
    _spec(tmp_path, "demo", TASKS_MD)
    state_path = tmp_path / TOMBSTONE_DIR / "state.json"

    stdin = {"cwd": str(tmp_path), "tool_input": {"path": str(tmp_path / "src/x.py")}}
    assert _run_hook(monkeypatch, stdin) == 0
    # The hook did nothing, so no state.json was written.
    assert not state_path.exists()


def test_post_tool_hook_fails_open_on_bad_stdin(tmp_path, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
    assert cli.main(["hook", "post-tool"]) == 0


def test_post_tool_hook_creates_snapshot_first_time(tmp_path, monkeypatch):
    _sidecar(tmp_path)
    tasks_path = _spec(tmp_path, "demo", TASKS_MD)
    state_path = tmp_path / TOMBSTONE_DIR / "state.json"

    # Bug 2: with no prior snapshot and no edit diff in the STDIN (an fs_write
    # has no oldStr to compare against), nothing is treated as newly checked.
    # The already-checked tasks 1.1 and 3.1 must not leak into turn.checked.
    stdin = {"cwd": str(tmp_path), "tool_input": {"path": str(tasks_path)}}
    assert _run_hook(monkeypatch, stdin) == 0

    state = json.loads(state_path.read_text())
    assert state["turn"]["checked"] == []
    # The snapshot is still created so later edits diff against it.
    assert state["tasks_snapshot"]["demo"]["3.2"] is False
    assert state["tasks_snapshot"]["demo"]["3.1"] is True


# A spec where several tasks are already [x]. The hook runs after the edit, so
# the files below are written in their POST-edit state.
DIFF_MD_3_DONE = """# Plan

- [x] 1. Done one
- [x] 2. Done two
- [x] 3. Target task
- [ ] 4. Later task
"""

DIFF_MD_4_DONE = """# Plan

- [x] 1. Done one
- [x] 2. Done two
- [x] 3. Target task
- [x] 4. Later task
"""


def test_post_tool_hook_first_run_uses_str_replace_diff(tmp_path, monkeypatch):
    # Bug 2: on the first run there is no snapshot, so the newly checked task
    # is read from the str_replace oldStr/newStr. Only task 3 flips here, even
    # though 1 and 2 are already checked in the file.
    _sidecar(tmp_path)
    tasks_path = _spec(tmp_path, "demo", DIFF_MD_3_DONE)
    state_path = tmp_path / TOMBSTONE_DIR / "state.json"

    stdin = {
        "cwd": str(tmp_path),
        "turn_id": "turn-A",
        "tool_input": {
            "path": str(tasks_path),
            "oldStr": "- [ ] 3. Target task\n",
            "newStr": "- [x] 3. Target task\n",
        },
    }
    assert _run_hook(monkeypatch, stdin) == 0

    state = json.loads(state_path.read_text())
    assert state["turn"]["checked"] == ["3"]
    assert state["turn"]["turn_id"] == "turn-A"


def test_post_tool_hook_resets_checked_on_new_turn(tmp_path, monkeypatch):
    # Bug 2: a task checked in an earlier turn must not leak into the next one.
    _sidecar(tmp_path)
    # The file already has task 4 checked (the edit just applied it).
    tasks_path = _spec(tmp_path, "demo", DIFF_MD_4_DONE)
    state_path = tmp_path / TOMBSTONE_DIR / "state.json"
    # A snapshot from the previous turn (4 still unchecked then), plus a
    # leftover checked id from that earlier turn that must be cleared.
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks_snapshot": {"demo": {"1": True, "2": True,
                                            "3": True, "4": False}},
                "turn": {"turn_id": "turn-A", "checked": ["3"]},
            }
        )
    )

    # A new turn edits task 4. The 3 from turn-A is cleared first, then the
    # snapshot diff records 4 as newly checked this turn.
    stdin = {
        "cwd": str(tmp_path),
        "turn_id": "turn-B",
        "tool_input": {
            "path": str(tasks_path),
            "oldStr": "- [ ] 4. Later task\n",
            "newStr": "- [x] 4. Later task\n",
        },
    }
    assert _run_hook(monkeypatch, stdin) == 0

    state = json.loads(state_path.read_text())
    assert state["turn"]["turn_id"] == "turn-B"
    assert state["turn"]["checked"] == ["4"]
