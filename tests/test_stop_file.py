"""Tests for the STOP file, the pre-tool block and resume (task 7.1)."""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone

from tombstone import cli, stop


def _run_pre_tool(project, monkeypatch, capsys):
    payload = json.dumps({"cwd": str(project), "session_id": "sess_x"})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    code = cli.main(["hook", "pre-tool"])
    return code, capsys.readouterr().err


def test_write_stop_fields(tmp_path):
    when = datetime(2026, 11, 5, 9, 30, 12, tzinfo=timezone.utc)
    path = stop.write_stop(
        tmp_path, "over_cap", "TOMBSTONE: task 11 over budget (est. 58 of 40). Stop.",
        task_id="11", estimate=58.0, requests_at_stop=1290, time=when,
        spec="demo", session_id="sess_1",
    )
    assert path == tmp_path / ".tombstone" / "STOP"
    data = json.loads(path.read_text())
    assert data["reason"] == "over_cap"
    assert data["task_id"] == "11"
    assert data["estimate"] == 58.0
    assert data["requests_at_stop"] == 1290
    assert data["time"] == "2026-11-05T09:30:12Z"
    assert data["message"].startswith("TOMBSTONE: task 11")


def test_pre_tool_blocks_with_message(tmp_path, monkeypatch, capsys):
    (tmp_path / ".kiro").mkdir()
    stop.write_stop(tmp_path, "stall", "TOMBSTONE: stalled. Stop.", task_id="1")
    code, err = _run_pre_tool(tmp_path, monkeypatch, capsys)
    assert code == 2
    assert err.strip() == "TOMBSTONE: stalled. Stop."


def test_pre_tool_blocks_repeatedly_until_resume(tmp_path, monkeypatch, capsys):
    (tmp_path / ".kiro").mkdir()
    stop.write_stop(tmp_path, "manual", "TOMBSTONE: manual stop.")
    for _ in range(3):
        code, _err = _run_pre_tool(tmp_path, monkeypatch, capsys)
        assert code == 2


def test_unreadable_stop_gives_generic_message(tmp_path, monkeypatch, capsys):
    (tmp_path / ".kiro").mkdir(exist_ok=True)
    (tmp_path / ".tombstone").mkdir(parents=True)
    (tmp_path / ".tombstone" / "STOP").write_text("{not json")
    code, err = _run_pre_tool(tmp_path, monkeypatch, capsys)
    assert code == 2
    assert err.strip() == stop.GENERIC_MESSAGE


def test_no_stop_allows(tmp_path, monkeypatch, capsys):
    (tmp_path / ".kiro").mkdir()
    code, err = _run_pre_tool(tmp_path, monkeypatch, capsys)
    assert code == 0
    assert err == ""


def test_resume_removes_stop_and_unblocks(tmp_path, monkeypatch, capsys):
    (tmp_path / ".kiro").mkdir()
    stop.write_stop(tmp_path, "over_cap", "TOMBSTONE: over.")
    assert cli.main(["resume", "--project", str(tmp_path)]) == 0
    assert not stop.stop_path(tmp_path).exists()
    capsys.readouterr()
    code, _err = _run_pre_tool(tmp_path, monkeypatch, capsys)
    assert code == 0


def test_resume_clears_state_stop_entry(tmp_path):
    sidecar = tmp_path / ".tombstone"
    sidecar.mkdir(parents=True)
    (sidecar / "state.json").write_text(
        json.dumps({"version": 1, "stop": {"time": "x"}, "keep": 1}))
    stop.write_stop(tmp_path, "manual", "m")
    assert stop.resume(tmp_path) is True
    state = json.loads((sidecar / "state.json").read_text())
    assert "stop" not in state and state["keep"] == 1


def test_resume_without_stop_is_harmless(tmp_path, capsys):
    (tmp_path / ".kiro").mkdir()
    assert cli.main(["resume", "--project", str(tmp_path)]) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert not (tmp_path / ".tombstone" / "state.json").exists()
