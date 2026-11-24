"""Task 24: live credits from ``creditSplit`` for the cap check (R4.5)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tombstone import caps, sessions, watcher

FIXTURE = (Path(__file__).parent / "fixtures" / "v3_live_credits"
           / "sess_9e1ffc6f-e6f4-44bc-9314-936ec75301e2")
HASH = "abc123"
SESS = "sess_9e1ffc6f-e6f4-44bc-9314-936ec75301e2"
NOW = datetime(2026, 11, 24, 13, 9, 6, tzinfo=timezone.utc)


def _lines() -> list[str]:
    return [l for l in (FIXTURE / "messages.jsonl").read_text("utf-8").splitlines() if l]


def _payload(line: str) -> dict:
    return json.loads(line)["payload"]


def _open_prefix() -> list[str]:
    """The file cut before ``usage_summary``: the open turn."""
    lines = _lines()
    cut = next(i for i, l in enumerate(lines) if _payload(l)["type"] == "usage_summary")
    return lines[:cut]


def _prefix_to_main(target: float) -> list[str]:
    """The file cut right after the creditSplit whose main is ``target``."""
    lines = _lines()
    for i, line in enumerate(lines):
        p = _payload(line)
        if p["type"] == "session_metadata" and p["key"] == "creditSplit" \
                and abs(p["value"]["main"] - target) < 5e-4:
            return lines[: i + 1]
    raise AssertionError("no creditSplit near the target")


def _write_session(root: Path, lines: list[str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "session.json").write_text(json.dumps({"id": SESS}), encoding="utf-8")
    (root / "messages.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def test_fixture_holds_no_home_paths():
    for path in FIXTURE.rglob("*"):
        if path.is_file():
            text = path.read_text("utf-8")
            assert "/Users/" not in text and "/home/" not in text


def test_live_credits_of_open_prefix_match_last_credit_split(tmp_path):
    lines = _open_prefix()
    root = _write_session(tmp_path / SESS, lines)
    splits = [_payload(l)["value"] for l in lines
              if _payload(l)["type"] == "session_metadata"
              and _payload(l)["key"] == "creditSplit"]
    assert sessions.live_credits(root) == pytest.approx(
        splits[-1]["main"] + splits[-1]["delegated"])
    assert sessions.live_credits(root / "messages.jsonl") == pytest.approx(
        splits[-1]["main"])


def test_live_credits_subtracts_the_split_before_turn_start(tmp_path):
    def ev(**p):
        return json.dumps({"timestamp": "2026-11-24T13:00:00.000Z", "payload": p})

    lines = [
        ev(type="session_metadata", key="creditSplit",
           value={"main": 1.0, "delegated": 0.5, "auxiliary": 9}),
        ev(type="turn_start", turnId="t1"),
        ev(type="session_metadata", key="creditSplit",
           value={"main": 1.25, "delegated": 0.75}),
    ]
    root = _write_session(tmp_path / SESS, lines)
    assert sessions.live_credits(root) == pytest.approx(0.5)


def test_live_credits_none_without_credit_split_or_open_turn(tmp_path):
    lines = [l for l in _open_prefix()
             if _payload(l).get("key") != "creditSplit"]
    root = _write_session(tmp_path / SESS, lines)
    assert sessions.live_credits(root) is None
    closed = _write_session(tmp_path / "closed" / SESS, _lines())
    assert sessions.live_credits(closed) is None


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "proj"
    (project / ".kiro").mkdir(parents=True)
    home = tmp_path / "kiro"
    root = home / "sessions" / HASH / SESS
    _write_session(root, [])
    (root / "session.json").write_text(json.dumps(
        {"id": SESS, "workspacePaths": [str(project)]}), encoding="utf-8")
    index = home / "session-index"
    index.mkdir(parents=True)
    (index / f"{HASH}.jsonl").write_text(json.dumps(
        {"op": "add", "sessionPath": f"{HASH}/{SESS}", "at": 1793955418488}) + "\n",
        encoding="utf-8")
    # A low calibration, as in the experiment: the estimate undershoots.
    watcher.update_state(project, lambda state: state.update(avg_unit_price=0.01))
    return project, home, root


def test_watcher_raises_over_cap_on_live_figure(tmp_path):
    project, home, root = _project(tmp_path)
    _write_session(root, _prefix_to_main(0.5977))
    (root / "session.json").write_text(json.dumps(
        {"id": SESS, "workspacePaths": [str(project)]}), encoding="utf-8")
    cfg = caps.Config(task_cap=0.33)

    event = watcher.poll_once(project, cfg, NOW, home)
    state = watcher.read_state(project)
    assert state["turn"]["estimate"] < 0.33
    assert state["turn"]["live"] == pytest.approx(0.5977, abs=1e-3)
    assert state["turn"]["live_basis"] == "live"
    assert event is not None and event.kind == "over_cap"
    assert "live 0.60 of 0.33" in event.message
    assert event.live == pytest.approx(0.5977, abs=1e-3)


def test_watcher_without_credit_split_keeps_the_estimate(tmp_path):
    project, home, root = _project(tmp_path)
    lines = [l for l in _prefix_to_main(0.5977)
             if _payload(l).get("key") != "creditSplit"]
    _write_session(root, lines)
    (root / "session.json").write_text(json.dumps(
        {"id": SESS, "workspacePaths": [str(project)]}), encoding="utf-8")
    event = watcher.poll_once(project, caps.Config(task_cap=0.33), NOW, home)
    state = watcher.read_state(project)
    assert event is None
    assert state["turn"]["live"] is None
    assert state["turn"]["estimate"] > 0


def test_evaluate_uses_estimate_when_it_is_higher():
    state = watcher.default_state()
    state["turn"] = {"watch_turn_id": "t1", "started_at": NOW.isoformat(),
                     "last_growth_at": NOW.isoformat(), "event_raised": False}
    obs = watcher.Observation(project="/p", engine="v3", turn_id="t1",
                              units=100, live=0.1)
    event = watcher.evaluate(state, obs, caps.Config(task_cap=0.5), NOW)
    assert event is not None and "est." in event.message
    assert "live" not in event.message


def test_hook_refuses_with_the_live_figure(tmp_path):
    spec = tmp_path / ".kiro" / "specs" / "demo"
    spec.mkdir(parents=True)
    (spec / "tasks.md").write_text("- [ ] 1. Next thing\n", encoding="utf-8")
    state_dir = tmp_path / ".tombstone"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(json.dumps(
        {"version": 1, "turn": {"estimate": 0.1, "live": 0.598,
                                "live_basis": "live"}}), encoding="utf-8")
    (tmp_path / "tombstone.toml").write_text("[caps]\nspec = 0.5\n", encoding="utf-8")
    message = caps.run_gate(tmp_path, {})
    assert message is not None
    assert "0.598 live spent" in message
    assert "(live)" in message
