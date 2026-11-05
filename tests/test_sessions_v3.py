"""Tests for the V3 session parser and fixtures (task 1.1)."""

from __future__ import annotations

from pathlib import Path

from tombstone.sessions import Session, Turn, detect_format, parse_session

FIXTURES = Path(__file__).parent / "fixtures"
MEAS5 = FIXTURES / "v3_measurement5" / "sess_8b5bdb0f-3662-4dca-be63-268a0365f3d9"
EDGE = FIXTURES / "edge"


def _only_turn(session: Session) -> Turn:
    assert len(session.turns) == 1, f"expected one turn, got {len(session.turns)}"
    return session.turns[0]


# --- measurement 5: the acceptance fixture ---------------------------------

def test_measurement5_cost_and_requests():
    session = parse_session(MEAS5)
    assert session.engine == "v3"
    turn = _only_turn(session)
    assert turn.state == "closed"
    assert turn.requests == 5
    assert turn.cost is not None
    # Acceptance: prints 0.390 credits, 5 requests.
    assert round(turn.cost, 3) == 0.390
    assert turn.unpriced_reason is None


def test_measurement5_model_and_times():
    session = parse_session(MEAS5)
    assert session.model == "auto"
    turn = _only_turn(session)
    assert turn.model == "auto"
    assert turn.start is not None
    assert turn.end is not None
    assert turn.end > turn.start


def test_measurement5_detect_format_dir_and_file():
    assert detect_format(MEAS5) == "v3"
    assert detect_format(MEAS5 / "messages.jsonl") == "v3"


def test_measurement5_fixture_has_no_home_path():
    text = (MEAS5 / "messages.jsonl").read_text(encoding="utf-8")
    text += (MEAS5 / "session.json").read_text(encoding="utf-8")
    for needle in ("sirenamc", "/private/tmp", "claude-501", "v3probe"):
        assert needle not in text, f"home information leaked: {needle}"


# --- edge fixtures ----------------------------------------------------------

def test_empty_session_no_turns_no_raise():
    session = parse_session(EDGE / "empty" / "sess_empty")
    assert session.engine == "v3"
    assert session.turns == []


def test_truncated_last_line_is_skipped_turn_stays_open():
    session = parse_session(EDGE / "truncated" / "sess_truncated")
    assert session.engine == "v3"
    turn = _only_turn(session)
    # The usage_summary line is broken, so the turn never closes.
    assert turn.state == "open"
    assert turn.cost is None


def test_unknown_format_returns_none():
    path = EDGE / "unknown" / "notasession.txt"
    assert detect_format(path) is None
    session = parse_session(path)
    assert session.engine is None
    assert session.turns == []
    assert session.unknown_reason == "unknown format"


def test_usage_summary_without_credit_entry_is_unpriced():
    session = parse_session(EDGE / "nocredit" / "sess_nocredit")
    turn = _only_turn(session)
    assert turn.state == "closed"
    assert turn.cost is None
    assert turn.unpriced_reason == "no credit entry in usage_summary"
    # Requests still counted from requestIds.
    assert turn.requests == 3


def test_open_turn_counts_assistant_events():
    session = parse_session(EDGE / "open" / "sess_open")
    turn = _only_turn(session)
    assert turn.state == "open"
    assert turn.end is None
    assert turn.cost is None
    # Two assistant events in the fixture.
    assert turn.requests == 2
    assert turn.start is not None


def test_parse_session_never_raises_on_missing_path(tmp_path):
    # A path that does not exist must not raise.
    session = parse_session(tmp_path / "does_not_exist")
    assert session.engine is None
