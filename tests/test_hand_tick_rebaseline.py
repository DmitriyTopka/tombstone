"""A checkbox ticked by hand between turns is not credited to the next turn."""

from __future__ import annotations

import io
import json
from pathlib import Path

from tombstone import cli
from tombstone.tombstones import refresh_snapshot, update_json

TASKS_OPEN = "- [ ] 1 Parent\n- [ ] 2.1 First\n- [ ] 2.2 Second\n"
TASKS_HAND = "- [x] 1 Parent\n- [ ] 2.1 First\n- [ ] 2.2 Second\n"
TASKS_AGENT = "- [x] 1 Parent\n- [x] 2.1 First\n- [ ] 2.2 Second\n"


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    (tmp_path / ".kiro" / "tombstone").mkdir(parents=True)
    tasks = tmp_path / ".kiro" / "specs" / "demo" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text(TASKS_OPEN, encoding="utf-8")
    return tmp_path, tasks, tmp_path / ".kiro" / "tombstone" / "state.json"


def _hook(event: str, payload: dict, monkeypatch) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return cli.main(["hook", event])


def _state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_snapshot(project: Path, state_path: Path) -> None:
    update_json(state_path,
                lambda s: refresh_snapshot(s, project, "demo", turn_id="t0"),
                default={"version": 1})


def test_hand_tick_between_turns_is_not_attributed(tmp_path, monkeypatch):
    project, tasks, state_path = _project(tmp_path)
    _seed_snapshot(project, state_path)
    tasks.write_text(TASKS_HAND, encoding="utf-8")  # human tick, no hook
    _hook("prompt", {"cwd": str(project), "prompt": "next job"}, monkeypatch)
    # Agent edits tasks.md without ticking anything new.
    _hook("post-tool", {"cwd": str(project), "tool_input": {"path": str(tasks)}},
          monkeypatch)
    state = _state(state_path)
    assert state["turn"]["checked"] == []
    assert state["tasks_snapshot"]["demo"]["1"] is True


def test_tick_during_the_turn_is_still_attributed(tmp_path, monkeypatch):
    project, tasks, state_path = _project(tmp_path)
    _seed_snapshot(project, state_path)
    tasks.write_text(TASKS_HAND, encoding="utf-8")
    _hook("prompt", {"cwd": str(project), "prompt": "next job"}, monkeypatch)
    tasks.write_text(TASKS_AGENT, encoding="utf-8")  # agent ticks 2.1
    _hook("post-tool", {"cwd": str(project), "tool_input": {"path": str(tasks)}},
          monkeypatch)
    assert _state(state_path)["turn"]["checked"] == ["2.1"]


def test_session_start_rebaselines(tmp_path, monkeypatch):
    project, tasks, state_path = _project(tmp_path)
    _seed_snapshot(project, state_path)
    tasks.write_text(TASKS_HAND, encoding="utf-8")
    _hook("session-start", {"cwd": str(project), "session_id": "s1"}, monkeypatch)
    state = _state(state_path)
    assert state["tasks_snapshot"]["demo"]["1"] is True
    assert not state.get("turn", {}).get("checked")
