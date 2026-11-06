"""Process lookup and kill (task 7.3, R6.3, R6.4, R6.5).

No real process is touched: ``ps``, ``lsof`` and ``os.kill`` are all mocked.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tombstone import stop, tombstones

STOP_TIME = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_real_signals(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("a real os.kill or subprocess call was attempted")

    monkeypatch.setattr(os, "kill", boom)
    monkeypatch.setattr(os, "killpg", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)


# The measured V3 tree: kiro-cli -> node agent -> sleep, plus other processes.
ROWS = [
    (1, 0, "/sbin/launchd"),
    (4529, 1, "/Users/x/.local/bin/kiro-cli chat --no-interactive -a"),
    (4539, 4529, "/Users/x/Library/Application Support/kiro-cli/node --flag kas"),
    (4556, 4539, "sleep 20"),
    (5000, 1, "/Users/x/.local/bin/kiro-cli chat"),  # another project
    (5001, 5000, "/Users/x/Library/Application Support/kiro-cli/node kas"),
    (6000, 1, "/usr/bin/vim notes.txt"),
]


def _cwds(project: Path):
    table = {4529: str(project), 4539: str(project), 5000: "/elsewhere"}
    return lambda pid: table.get(pid)


def _find(project: Path, rows=ROWS, cwd=None):
    return stop.find_project_processes(
        project, list_processes=lambda: list(rows),
        process_cwd=cwd or _cwds(project),
    )


def _write_state(project: Path, entry: dict) -> Path:
    path = project / ".kiro" / "tombstone" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "stop": entry}), encoding="utf-8")
    return path


def _stop_entry(**extra) -> dict:
    entry = {"time": "2026-11-06T12:00:00Z", "requests_at_stop": 10}
    entry.update(extra)
    return entry


# --- find_project_processes ---------------------------------------------------


def test_find_returns_tree_leaves_first_root_last(tmp_path):
    assert _find(tmp_path) == [4556, 4539, 4529]


def test_find_skips_other_projects_and_unrelated_processes(tmp_path):
    found = _find(tmp_path)
    for pid in (1, 5000, 5001, 6000):
        assert pid not in found


def test_find_returns_empty_without_a_match(tmp_path):
    assert _find(tmp_path, cwd=lambda pid: "/elsewhere") == []
    assert _find(tmp_path, rows=[]) == []


def test_find_cwd_must_match_exactly(tmp_path):
    child = tmp_path / "sub"
    child.mkdir()
    assert _find(child, cwd=lambda pid: str(tmp_path)) == []


def test_find_never_returns_own_pid_or_ancestors(tmp_path):
    me = os.getpid()
    rows = [
        (1, 0, "/sbin/launchd"),
        (100, 1, "/bin/kiro-cli chat"),
        (me, 100, "python -m tombstone watch"),
        (me + 1, me, "sleep 1"),
    ]
    found = _find(tmp_path, rows=rows, cwd=lambda pid: str(tmp_path))
    assert me not in found
    assert 100 not in found
    assert 1 not in found


def test_find_orders_deeper_processes_first(tmp_path):
    rows = [
        (10, 1, "kiro-cli chat"),
        (11, 10, "node agent"),
        (12, 11, "bash -c tool"),
        (13, 12, "sleep 5"),
        (14, 11, "other tool"),
    ]
    found = _find(tmp_path, rows=rows, cwd=lambda pid: str(tmp_path))
    assert found == [13, 12, 14, 11, 10]


def test_list_processes_parses_ps_output(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    out = ("    1     0 /sbin/launchd\n"
           " 4529     1 /Users/x/.local/bin/kiro-cli chat --no-interactive\n"
           " 4539  4529 /Users/x/Library/Application Support/kiro-cli/node a\n"
           "garbage line\n")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rows = stop._list_processes()
    assert seen["cmd"] == ["ps", "-Ao", "pid=,ppid=,command="]
    assert rows[1] == (4529, 1, "/Users/x/.local/bin/kiro-cli chat --no-interactive")
    assert rows[2][0:2] == (4539, 4529)
    assert len(rows) == 3


def test_process_cwd_parses_lsof(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout="p4529\nfcwd\nn/Users/x/proj dir\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert stop._process_cwd(4529) == "/Users/x/proj dir"
    assert seen["cmd"] == ["lsof", "-a", "-p", "4529", "-d", "cwd", "-Fn"]


def test_process_cwd_none_when_lsof_fails(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    def fake_run(cmd, **kwargs):
        raise OSError("no lsof")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert stop._process_cwd(1) is None


# --- maybe_kill ---------------------------------------------------------------


class FakeKernel:
    """Records signals. ``stubborn`` pids survive SIGTERM."""

    def __init__(self, pids, stubborn=()):
        self.alive = set(pids)
        self.stubborn = set(stubborn)
        self.sent: list[tuple[int, int]] = []

    def kill(self, pid, sig):
        if pid not in self.alive:
            raise ProcessLookupError(pid)
        if sig == 0:
            return
        self.sent.append((pid, sig))
        if sig == signal.SIGKILL or pid not in self.stubborn:
            self.alive.discard(pid)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _run(project, kernel, *, requests_now=12, offset=40, pids=(4556, 4539, 4529),
         clock=None, **extra):
    clock = clock or FakeClock()
    return stop.maybe_kill(
        project, requests_now=requests_now,
        now=STOP_TIME + timedelta(seconds=offset), grace_seconds=30,
        find=lambda p: list(pids), kill_fn=kernel.kill,
        sleep=clock.sleep, clock=clock.clock, **extra)


def _state(project) -> dict:
    return json.loads((project / ".kiro" / "tombstone" / "state.json").read_text())


def test_sigterm_leaves_first_root_last(tmp_path):
    _write_state(tmp_path, _stop_entry())
    kernel = FakeKernel([4556, 4539, 4529])
    result = _run(tmp_path, kernel)
    assert kernel.sent == [(4556, signal.SIGTERM), (4539, signal.SIGTERM),
                           (4529, signal.SIGTERM)]
    assert result["status"] == "killed"
    assert result["killed"] == []
    assert _state(tmp_path)["stop"]["kill"]["terminated"] == [4556, 4539, 4529]


def test_sigkill_only_for_survivors_after_five_seconds(tmp_path):
    _write_state(tmp_path, _stop_entry())
    kernel = FakeKernel([4556, 4539, 4529], stubborn=[4539])
    clock = FakeClock()
    result = _run(tmp_path, kernel, clock=clock)
    assert kernel.sent[-1] == (4539, signal.SIGKILL)
    assert [s for s in kernel.sent if s[1] == signal.SIGKILL] == [(4539, signal.SIGKILL)]
    assert clock.now >= 5.0
    assert result["killed"] == [4539]


def test_no_sigkill_wait_when_everything_exits(tmp_path):
    _write_state(tmp_path, _stop_entry())
    kernel = FakeKernel([4556, 4539, 4529])
    clock = FakeClock()
    _run(tmp_path, kernel, clock=clock)
    assert clock.now == 0.0


def test_waits_for_the_grace(tmp_path):
    _write_state(tmp_path, _stop_entry())
    kernel = FakeKernel([4529])
    result = _run(tmp_path, kernel, offset=10)
    assert result["status"] == "waiting_grace"
    assert kernel.sent == []
    assert "kill" not in _state(tmp_path)["stop"]


def test_needs_a_growing_request_count(tmp_path):
    _write_state(tmp_path, _stop_entry())
    kernel = FakeKernel([4529])
    assert _run(tmp_path, kernel, requests_now=10)["status"] == "not_growing"
    assert _run(tmp_path, kernel, requests_now=None)["status"] == "not_growing"
    assert kernel.sent == []


def test_no_match_skips_and_warns_in_state(tmp_path):
    _write_state(tmp_path, _stop_entry())
    kernel = FakeKernel([4529])
    result = _run(tmp_path, kernel, pids=())
    assert result["status"] == "no_process"
    assert result["warning"] == stop.NO_PROCESS_WARNING
    assert kernel.sent == []
    entry = _state(tmp_path)["stop"]
    assert entry["warnings"] == [stop.NO_PROCESS_WARNING]
    assert entry["kill"]["status"] == "no_process"


def test_runs_only_once_per_stop(tmp_path):
    _write_state(tmp_path, _stop_entry())
    kernel = FakeKernel([4556, 4539, 4529])
    _run(tmp_path, kernel)
    sent = list(kernel.sent)
    assert _run(tmp_path, kernel)["status"] == "done"
    assert kernel.sent == sent


def test_no_stop_entry_means_no_action(tmp_path):
    kernel = FakeKernel([4529])
    assert _run(tmp_path, kernel)["status"] == "no_stop"
    (tmp_path / ".kiro" / "tombstone").mkdir(parents=True)
    (tmp_path / ".kiro" / "tombstone" / "state.json").write_text('{"version": 1}')
    assert _run(tmp_path, kernel)["status"] == "no_stop"
    assert kernel.sent == []


def test_vanished_process_is_not_an_error(tmp_path):
    _write_state(tmp_path, _stop_entry())
    kernel = FakeKernel([4529])  # 4556 and 4539 already gone
    result = _run(tmp_path, kernel)
    assert result["terminated"] == [4529]


def test_never_signals_pid_one_or_self(tmp_path):
    _write_state(tmp_path, _stop_entry())
    kernel = FakeKernel([1, os.getpid(), 4529])
    _run(tmp_path, kernel, pids=(1, os.getpid(), 4529))
    assert [pid for pid, _ in kernel.sent] == [4529]


def test_project_files_are_untouched(tmp_path):
    (tmp_path / "keep.txt").write_text("data")
    (tmp_path / "tombstones.json").write_text("{}")
    _write_state(tmp_path, _stop_entry())
    kernel = FakeKernel([4556, 4539, 4529])
    _run(tmp_path, kernel)
    assert (tmp_path / "keep.txt").read_text() == "data"
    assert (tmp_path / "tombstones.json").read_text() == "{}"
    names = {p.name for p in tmp_path.iterdir()}
    assert names == {"keep.txt", "tombstones.json", ".kiro"}


def test_spawn_reconcile_passes_a_short_timeout(tmp_path, monkeypatch):
    seen = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            seen["args"] = args

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    tombstones.spawn_reconcile(tmp_path, "sess", "t1", timeout_s=1)
    args = seen["args"]
    assert args[args.index("--timeout") + 1] == "1.0"
