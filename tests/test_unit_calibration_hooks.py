"""R4.1: the V3 unit calibration also runs on the hook paths (hooks-only mode).

Without the watcher daemon, ``avg_unit_price`` used to stay None, so the open
turn estimate fell back to ``units x avg_request_price`` and ran 1.4x to 3.7x
over the actual. These tests use the real V3 measurement fixture
(``v3_measurement5``: 9 assistant events, 5 requests, actual 0.39).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tombstone import sessions, tombstones, watcher

FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "v3_measurement5"
           / "sess_8b5bdb0f-3662-4dca-be63-268a0365f3d9")
OLD_UUID = "8b5bdb0f-3662-4dca-be63-268a0365f3d9"
OLD_TURN = "dec02aaa-3791-48a3-8d7b-a19c21303a9f"
NOW = datetime(2026, 11, 6, 9, 0, 0, tzinfo=timezone.utc)


def _copy_session(kiro_home: Path, suffix: str) -> tuple[str, str]:
    """Copy the fixture under new session and turn ids. Returns (session, turn)."""
    new_uuid = OLD_UUID[:-2] + suffix
    new_turn = OLD_TURN[:-2] + suffix
    dest = kiro_home / "sessions" / "hash0" / f"sess_{new_uuid}"
    shutil.copytree(FIXTURE, dest)
    for name in ("messages.jsonl", "session.json"):
        path = dest / name
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(OLD_UUID, new_uuid).replace(OLD_TURN, new_turn),
                        encoding="utf-8")
    return new_uuid, new_turn


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


@pytest.fixture(autouse=True)
def _no_spawn(monkeypatch):
    monkeypatch.setattr(tombstones, "spawn_reconcile", lambda *a, **k: None)


def _state(project: Path) -> dict:
    return json.loads((project / ".kiro" / "tombstone" / "state.json")
                      .read_text(encoding="utf-8"))


def test_hooks_only_calibrates_and_estimate_is_within_30_percent(tmp_path):
    kiro_home = tmp_path / "kiro_home"
    project = _project(tmp_path)
    first, first_turn = _copy_session(kiro_home, "01")
    second, second_turn = _copy_session(kiro_home, "02")

    # No watcher anywhere: only the Stop hook sees the first closed turn.
    tombstones.on_turn_end(project, {"session_id": first}, kiro_home=kiro_home)
    state = _state(project)
    assert state["avg_unit_price"] is not None
    assert state["unit_turns"] == [first_turn]
    assert state["unit_window"] == [[9, pytest.approx(0.3901, abs=1e-4)]]

    # The next turn is judged by units x avg_unit_price (est.) against its actual.
    path = sessions.find_session(project, second, kiro_home=kiro_home)
    turn = sessions.parse_session(path).turns[0]
    units = sessions.assistant_counts(path)[second_turn]
    price, basis = watcher.unit_price(state, "v3", tombstones.DEFAULT_AVG_REQUEST_PRICE)
    assert basis == "calibrated"
    estimate = units * price
    error = abs(estimate - turn.cost) / turn.cost
    assert error <= 0.30, f"est. {estimate:.3f} vs actual {turn.cost:.3f}"

    # The old fallback (units x avg_request_price) was far outside the bound.
    old_estimate = units * tombstones._avg_price(state, tombstones.DEFAULT_AVG_REQUEST_PRICE)
    assert abs(old_estimate - turn.cost) / turn.cost > 0.30


def test_reconcile_path_also_calibrates(tmp_path):
    kiro_home = tmp_path / "kiro_home"
    project = _project(tmp_path)
    sid, turn_id = _copy_session(kiro_home, "03")
    path = sessions.find_session(project, sid, kiro_home=kiro_home)
    turn = sessions.parse_session(path).turns[0]

    tombstones.on_turn_end(project, {"session_id": sid}, kiro_home=kiro_home)
    # Forget the calibration, then let the reconcile child path fill the turn.
    tombstones.update_json(
        project / ".kiro" / "tombstone" / "state.json",
        lambda s: s.update(unit_window=[], unit_turns=[], avg_unit_price=None) or s,
        default={"version": 1},
    )
    tombstones._fill_turn(project, turn, path)
    state = _state(project)
    assert state["unit_turns"] == [turn_id]
    assert state["avg_unit_price"] == pytest.approx(turn.cost / 9, rel=1e-4)


def test_turn_seen_by_watcher_and_hook_is_folded_once(tmp_path):
    kiro_home = tmp_path / "kiro_home"
    project = _project(tmp_path)
    sid, turn_id = _copy_session(kiro_home, "04")
    watcher.update_state(project, lambda s: s.update(hook_session_id=sid))

    # Watcher first, then the hook.
    cfg = type("Cfg", (), {"avg_request_price": 0.25})()
    # poll_once needs a full config object only for evaluate on an open turn;
    # the turn is closed, so the calibration step is what runs.
    watcher.poll_once(project, cfg, NOW, kiro_home=kiro_home)
    after_watcher = _state(project)
    assert after_watcher["unit_turns"] == [turn_id]
    assert len(after_watcher["unit_window"]) == 1

    tombstones.on_turn_end(project, {"session_id": sid}, kiro_home=kiro_home)
    after_hook = _state(project)
    assert after_hook["unit_turns"] == [turn_id]
    assert after_hook["unit_window"] == after_watcher["unit_window"]
    assert after_hook["avg_unit_price"] == after_watcher["avg_unit_price"]

    # And the other way round: the watcher polls again, still one entry.
    watcher.poll_once(project, cfg, NOW, kiro_home=kiro_home)
    assert _state(project)["unit_window"] == after_watcher["unit_window"]


def test_turn_seen_by_hook_then_watcher_is_folded_once(tmp_path):
    kiro_home = tmp_path / "kiro_home"
    project = _project(tmp_path)
    sid, turn_id = _copy_session(kiro_home, "05")

    tombstones.on_turn_end(project, {"session_id": sid}, kiro_home=kiro_home)
    after_hook = _state(project)
    assert after_hook["unit_turns"] == [turn_id]

    watcher.update_state(project, lambda s: s.update(hook_session_id=sid))
    cfg = type("Cfg", (), {"avg_request_price": 0.25})()
    watcher.poll_once(project, cfg, NOW, kiro_home=kiro_home)
    after_both = _state(project)
    assert after_both["unit_turns"] == [turn_id]
    assert after_both["unit_window"] == after_hook["unit_window"]


def test_fold_unit_turn_dedupes_by_turn_id():
    state: dict = {}
    assert tombstones.fold_unit_turn(state, "t1", 9, 0.39) is True
    assert tombstones.fold_unit_turn(state, "t1", 9, 0.39) is False
    assert state["unit_window"] == [[9, 0.39]]
    assert state["avg_unit_price"] == pytest.approx(0.39 / 9, rel=1e-4)
