"""Tests for the task-start gate and the pre-tool hook (task 4.2)."""

from __future__ import annotations

import io
import json
from pathlib import Path

from tombstone import caps, cli, tombstones
from tombstone.caps import Config, check_task_start
from tombstone.tombstones import TaskRef


def _task(task_id="4.2", name="Add stall evaluation", spec="demo"):
    return TaskRef(spec=spec, task_id=task_id, name=name, source="test")


def _rec(task_id, name, cost, spec="demo", outcome="finished", basis="actual"):
    record = {
        "id": f"{spec}/{task_id}", "task_id": task_id, "name": name,
        "spec": spec, "outcome": outcome, "estimate": cost,
        "actual": cost if basis == "actual" else None,
    }
    return record


# --- check_task_start -------------------------------------------------------


def test_allows_when_budget_is_enough():
    config = Config(task_cap=40, spec_cap=300)
    decision = check_task_start(config, [], {}, _task())
    assert decision.allow and decision.new_task


def test_same_task_is_always_allowed():
    config = Config(task_cap=40, spec_cap=10)
    state = {"current_task": {"spec": "demo", "task_id": "4.2"}}
    decision = check_task_start(config, [], state, _task())
    assert decision.allow and not decision.new_task


def test_blocks_when_remaining_below_similar_median():
    config = Config(task_cap=40, spec_cap=300)
    records = [
        _rec("1", "Write parser tests", 100.0),
        _rec("2", "Write stall evaluation", 10.0),
        _rec("3", "Write stall evaluation again", 14.0),
        _rec("9", "Other work", 180.0),
    ]
    records[3] = _rec("9", "Other work", 170.0)  # spent 294, remaining 6
    decision = check_task_start(config, records, {}, _task())
    assert not decision.allow
    assert decision.outcome == "cap"
    assert 'task 4.2 "Add stall evaluation"' in decision.message
    assert "Similar tasks cost 12.000 (actual, median of 2)" in decision.message
    assert '6.000 of 300.000 left (actual)' in decision.message


def test_no_similar_uses_task_cap_and_says_so():
    config = Config(task_cap=40, spec_cap=300)
    records = [_rec("1", "Unrelated thing", 270.0)]  # remaining 30 < 40
    decision = check_task_start(config, records, {}, _task())
    assert not decision.allow
    assert "No similar task, using task cap 40.000 (est. reference)" in decision.message
    assert "30.000 of 300.000 left (actual)" in decision.message


def test_spec_cap_reached_blocks():
    config = Config(task_cap=1, spec_cap=300)
    records = [_rec("1", "Unrelated thing", 300.0)]
    decision = check_task_start(config, records, {}, _task())
    assert not decision.allow and decision.outcome == "cap"
    assert "Spec cap reached" in decision.message


def test_open_turn_estimate_counts_and_labels_est():
    config = Config(task_cap=40, spec_cap=300)
    state = {"turn": {"estimate": 280.0}}
    decision = check_task_start(config, [], state, _task())
    assert not decision.allow
    assert "20.000 of 300.000 left (est.)" in decision.message


def test_estimate_only_records_label_est():
    config = Config(task_cap=40, spec_cap=300)
    records = [_rec("1", "Unrelated thing", 270.0, basis="est.")]
    decision = check_task_start(config, records, {}, _task())
    assert "(est.)" in decision.message.split("left")[1]


def test_cap_records_are_not_counted_as_spend():
    config = Config(task_cap=40, spec_cap=300)
    records = [_rec("5", "Blocked", 0.0, outcome="cap")]
    spent, basis = caps.spec_spent(records, "demo")
    assert spent == 0.0 and basis == "actual"
    assert check_task_start(config, records, {}, _task()).allow


def test_plan_balance_below_minimum_blocks_only_when_known():
    config = Config(min_plan_balance=5.0)
    assert not check_task_start(config, [], {}, _task(), balance=2.0).allow
    assert check_task_start(config, [], {}, _task(), balance=None).allow
    assert check_task_start(Config(), [], {}, _task(), balance=0.5).allow


# --- run_gate and the hook --------------------------------------------------


def _project(tmp_path: Path, tasks: str = None) -> Path:
    spec = tmp_path / ".kiro" / "specs" / "demo"
    spec.mkdir(parents=True)
    (spec / "tasks.md").write_text(
        tasks or "- [x] 1. Done thing\n- [ ] 2. Write stall evaluation\n",
        encoding="utf-8",
    )
    return tmp_path


def test_run_gate_allows_and_sets_current_task(tmp_path):
    project = _project(tmp_path)
    assert caps.run_gate(project, {}) is None
    state = json.loads((project / ".tombstone/state.json").read_text())
    assert state["current_task"] == {"spec": "demo", "task_id": "2"}


def test_run_gate_blocks_and_writes_one_cap_record(tmp_path):
    project = _project(tmp_path)
    (project / "tombstone.toml").write_text("[caps]\nspec = 10\n", encoding="utf-8")
    first = caps.run_gate(project, {})
    second = caps.run_gate(project, {})
    assert first and first == second
    assert 'task 2 "Write stall evaluation"' in first
    store = json.loads((project / "tombstones.json").read_text())
    caps_records = [r for r in store["tombstones"] if r["outcome"] == "cap"]
    assert len(caps_records) == 1
    assert caps_records[0]["id"] == "demo/2"
    assert caps_records[0]["turns"] == []
    state_file = project / ".tombstone/state.json"
    assert not state_file.exists() or "current_task" not in json.loads(
        state_file.read_text())


def test_raising_the_cap_unblocks_next_call(tmp_path):
    project = _project(tmp_path)
    toml = project / "tombstone.toml"
    toml.write_text("[caps]\nspec = 10\n", encoding="utf-8")
    assert caps.run_gate(project, {})
    toml.write_text("[caps]\nspec = 300\n", encoding="utf-8")
    assert caps.run_gate(project, {}) is None


def test_existing_record_is_not_overwritten_by_cap(tmp_path):
    project = _project(tmp_path)
    (project / "tombstone.toml").write_text("[caps]\nspec = 10\n", encoding="utf-8")
    (project / "tombstones.json").write_text(json.dumps({
        "version": 1,
        "tombstones": [_rec("2", "Write stall evaluation", 9.5)],
    }), encoding="utf-8")
    assert caps.run_gate(project, {})
    store = json.loads((project / "tombstones.json").read_text())
    assert [r["outcome"] for r in store["tombstones"]] == ["finished"]


def test_stdin_task_id_is_used(tmp_path):
    project = _project(tmp_path)
    assert caps.run_gate(project, {"task_id": "1"}) is None
    state = json.loads((project / ".tombstone/state.json").read_text())
    assert state["current_task"]["task_id"] == "1"


def test_no_spec_means_no_gate(tmp_path):
    (tmp_path / ".kiro").mkdir()
    assert caps.run_gate(tmp_path, {}) is None


def _run_hook(monkeypatch, capsys, project, payload=None):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        payload if payload is not None else {"cwd": str(project)})))
    code = cli.main(["hook", "pre-tool"])
    return code, capsys.readouterr()


def test_hook_pre_tool_exit_codes(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path)
    code, out = _run_hook(monkeypatch, capsys, project)
    assert code == 0 and out.err == ""

    (project / ".tombstone/state.json").unlink()
    (project / "tombstone.toml").write_text("[caps]\nspec = 10\n", encoding="utf-8")
    code, out = _run_hook(monkeypatch, capsys, project)
    assert code == 2
    assert out.err.startswith("TOMBSTONE: task 2")
    assert out.err.count("\n") == 1


def test_hook_pre_tool_fails_open(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(caps, "run_gate", boom)
    code, out = _run_hook(monkeypatch, capsys, project)
    assert code == 0
    assert "boom" in (project / ".tombstone/errors.log").read_text()


def test_hook_pre_tool_empty_stdin_is_ok(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert cli.main(["hook", "pre-tool"]) == 0
