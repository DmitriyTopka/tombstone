"""Task 20.1: the receipt records a kill that ran (R6.3, R7.1)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from tombstone import autopsy, stop

RECEIPT = """# Autopsy: task unknown

- Task id: unknown
- STOP time: 2026-11-06T12:00:00Z
- STOP reason: over_cap
- Refusal text: stop
- Step two (kill): no (not yet; waits for the grace period and more requests after the stop)
- Plan balance at stop (est.): unknown

## Last 10 tool calls

No tool calls found in the session.
"""
LATER = datetime(2026, 11, 6, 12, 5, 0, tzinfo=timezone.utc)


def _setup(tmp_path: Path, raw="auto") -> Path:
    receipt = tmp_path / ".tombstone" / "autopsy" / "2026-11-06_unknown-t.md"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(RECEIPT, encoding="utf-8")
    entry = {"time": "2026-11-06T12:00:00Z", "requests_at_stop": 10,
             "autopsy": str(receipt) if raw == "auto" else raw}
    (tmp_path / ".tombstone" / "state.json").write_text(
        json.dumps({"version": 1, "stop": entry}), encoding="utf-8")
    return receipt


def _run(tmp_path, pids):
    alive = set(pids)

    def kill_fn(pid, sig):
        if sig == 0:
            if pid in alive:
                return None
            raise ProcessLookupError
        alive.discard(pid)

    return stop.maybe_kill(tmp_path, requests_now=12, now=LATER,
                           find=lambda p: pids, kill_fn=kill_fn,
                           sleep=lambda s: None)


def test_killed_line_has_counts_and_graveyard_reads_ran(tmp_path):
    receipt = _setup(tmp_path)
    assert _run(tmp_path, [4529, 4539, 4556])["status"] == "killed"
    text = receipt.read_text(encoding="utf-8")
    assert "- Step two (kill): yes (ran at " in text
    assert "3 processes terminated, 0 killed)" in text
    assert text.replace(text.split("Step two (kill): ")[1].split("\n")[0], "") \
        == RECEIPT.replace(RECEIPT.split("Step two (kill): ")[1].split("\n")[0], "")
    parsed = autopsy.parse_autopsy(text, receipt.name)
    assert parsed["kill_ran"] is True


def test_no_process_writes_warning(tmp_path):
    receipt = _setup(tmp_path)
    assert _run(tmp_path, [])["status"] == "no_process"
    line = [x for x in receipt.read_text(encoding="utf-8").splitlines()
            if x.startswith("- Step two")][0]
    assert line.startswith("- Step two (kill): skipped at ")
    assert stop.NO_PROCESS_WARNING in line
    parsed = autopsy.parse_autopsy(receipt.read_text(encoding="utf-8"), receipt.name)
    assert parsed["kill_ran"] is False


def test_missing_autopsy_does_not_raise(tmp_path):
    receipt = _setup(tmp_path)
    receipt.unlink()
    assert _run(tmp_path, [4529])["status"] == "killed"
    state = json.loads((tmp_path / ".tombstone" / "state.json").read_text())
    assert state["stop"]["kill"]["status"] == "killed"


def test_no_autopsy_path_does_not_raise(tmp_path):
    _setup(tmp_path, raw=None)
    assert _run(tmp_path, [4529])["status"] == "killed"


def test_symlinked_autopsy_not_followed(tmp_path):
    receipt = _setup(tmp_path)
    target = tmp_path / "outside.md"
    target.write_text(RECEIPT, encoding="utf-8")
    receipt.unlink()
    os.symlink(target, receipt)
    assert _run(tmp_path, [4529])["status"] == "killed"
    assert target.read_text(encoding="utf-8") == RECEIPT
    assert receipt.is_symlink()
