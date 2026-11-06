"""Every writer under .kiro/tombstone refuses a symlinked sidecar.

``.kiro/tombstone`` is a symlink to a directory outside the project. Each
writer must write nothing there, print one stderr line, and not raise. All
paths live under ``tmp_path``.
"""

from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tombstone import autopsy, caps, cli, stop, tombstones
from tests.test_autopsy import HOME, make_session, stop_entry


@pytest.fixture
def linked(tmp_path):
    """A project whose sidecar is a symlink to an outside directory."""
    project = tmp_path / "proj"
    outside = tmp_path / "outside"
    (project / ".kiro").mkdir(parents=True)
    outside.mkdir()
    os.symlink(outside, project / ".kiro" / "tombstone")
    return project, outside


def _empty(outside: Path) -> bool:
    return list(outside.rglob("*")) == []


def _skipped(capsys) -> bool:
    return "write skipped" in capsys.readouterr().err


def test_write_stop(linked, capsys):
    project, outside = linked
    stop.write_stop(project, "manual", "TOMBSTONE: stop.")
    assert _empty(outside)
    assert _skipped(capsys)


def test_resume_keeps_a_stop_behind_a_symlink(linked, capsys):
    project, outside = linked
    (outside / "STOP").write_text("{}", encoding="utf-8")
    assert stop.resume(project) is False
    assert (outside / "STOP").exists()
    assert _skipped(capsys)


def test_resume_state_clear_is_refused(linked, capsys):
    project, outside = linked
    (outside / "state.json").write_text('{"stop": {"a": 1}}', encoding="utf-8")
    stop.resume(project)
    assert json.loads((outside / "state.json").read_text())["stop"] == {"a": 1}
    assert not (outside / "state.json.lock").exists()
    assert _skipped(capsys)


def test_stop_error_log(linked, capsys):
    project, outside = linked
    stop._log_error(project, "boom")
    assert _empty(outside)
    assert _skipped(capsys)


def test_usage_cache(linked, capsys, monkeypatch):
    project, outside = linked
    monkeypatch.setattr(caps, "_probe_usage",
                        lambda: ({"balance": 10.0}, None))
    now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    caps.plan_balance(project, now, refresh=True)
    assert _empty(outside)
    assert _skipped(capsys)


def test_hook_error_log(linked, capsys, monkeypatch):
    project, outside = linked
    monkeypatch.setattr(cli, "_PROJECT_OVERRIDE", project)
    cli._log_hook_error({}, "boom")
    assert _empty(outside)
    assert _skipped(capsys)


def test_store_warning_log(linked, capsys):
    project, outside = linked
    tombstones._log_warning(project / ".kiro" / "tombstone", "warn")
    assert _empty(outside)
    assert _skipped(capsys)


def test_update_json_and_pending(linked, capsys):
    project, outside = linked
    path = project / ".kiro" / "tombstone" / "state.json"
    result = tombstones.update_json(path, lambda d: {**d, "n": 1}, default={})
    assert result == {"n": 1}
    assert _empty(outside)
    assert _skipped(capsys)


def test_reconcile_log(linked, capsys):
    project, outside = linked
    tombstones.reconcile_log(project, "line")
    assert _empty(outside)
    assert _skipped(capsys)


def test_reconcile_timeout_error_log(linked, capsys):
    project, outside = linked
    tombstones._log_reconcile_timeout(project, "sess", "t1")
    assert _empty(outside)
    assert _skipped(capsys)


def test_spawn_reconcile_log_sink(linked, capsys, monkeypatch):
    import subprocess

    project, outside = linked
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(k))
    tombstones.spawn_reconcile(project, "sess", "t1")
    assert len(calls) == 1
    assert calls[0]["stdout"] is subprocess.DEVNULL
    assert _empty(outside)
    assert _skipped(capsys)


def test_autopsy_write(linked, tmp_path, capsys):
    project, outside = linked
    result = autopsy.write_autopsy(project, stop_entry(), make_session(tmp_path),
                                   {}, home=HOME)
    assert result is None
    assert _empty(outside)
    assert _skipped(capsys)


def test_autopsy_append(linked, capsys):
    project, outside = linked
    receipt = outside / "autopsy" / "2026-11-06_demo-2-1.md"
    receipt.parent.mkdir()
    receipt.write_text("# receipt\n", encoding="utf-8")
    seen = project / ".kiro" / "tombstone" / "autopsy" / receipt.name
    assert autopsy.append_actual(seen, None, HOME) is False
    assert receipt.read_text(encoding="utf-8") == "# receipt\n"
    assert _skipped(capsys)


def test_graveyard(linked, capsys):
    project, outside = linked
    tombstones.write_graveyard(project)
    assert _empty(outside)
    assert _skipped(capsys)


def test_hooks_still_exit_zero(linked, monkeypatch, capsys):
    project, outside = linked
    for event in ("prompt", "post-tool", "stop", "session-start"):
        payload = json.dumps({"cwd": str(project), "session_id": "s",
                              "prompt": "hello"})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        assert cli.main(["hook", event, "--project", str(project)]) == 0
    assert _empty(outside)
    assert "write skipped" in capsys.readouterr().err


def test_pre_tool_still_exits_two_with_a_real_stop(tmp_path, monkeypatch, capsys):
    (tmp_path / ".kiro").mkdir()
    stop.write_stop(tmp_path, "manual", "TOMBSTONE: stopped. Stop.")
    assert (tmp_path / ".kiro" / "tombstone" / "STOP").is_file()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(tmp_path)})))
    assert cli.main(["hook", "pre-tool", "--project", str(tmp_path)]) == 2
    assert "TOMBSTONE: stopped" in capsys.readouterr().err
