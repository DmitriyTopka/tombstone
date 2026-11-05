"""Tests for the V2 (legacy cli) session parser and fixtures (task 1.2)."""

from __future__ import annotations

from pathlib import Path

from tombstone.sessions import Session, Turn, detect_format, parse_session

FIXTURES = Path(__file__).parent / "fixtures"
MEAS2 = FIXTURES / "v2_measurement2" / "b7b3fa2c-07c1-4f70-b486-b566c4860883.json"
EDGE = FIXTURES / "edge"


def _only_turn(session: Session) -> Turn:
    assert len(session.turns) == 1, f"expected one turn, got {len(session.turns)}"
    return session.turns[0]


# --- measurement 2: the real scrubbed V2 fixture ---------------------------

def test_measurement2_cost_and_requests():
    session = parse_session(MEAS2)
    assert session.engine == "v2"
    turn = _only_turn(session)
    assert turn.state == "closed"
    # total_request_count is 24.
    assert turn.requests == 24
    assert turn.cost is not None
    # Sum of the 24 credit metering values, rounds to 1.156.
    assert round(turn.cost, 3) == 1.156
    assert turn.unpriced_reason is None


def test_measurement2_model_and_times():
    session = parse_session(MEAS2)
    assert session.model == "auto"
    turn = _only_turn(session)
    assert turn.model == "auto"
    assert turn.start is not None
    assert turn.end is not None
    # end comes from end_timestamp.
    assert turn.end.isoformat() == "2026-11-03T08:52:23.432850+00:00"
    # start is end minus turn_duration (63.164698375 s).
    assert turn.end > turn.start
    span = (turn.end - turn.start).total_seconds()
    assert abs(span - 63.164698375) < 1e-6


def test_measurement2_cwd_is_the_scrubbed_workspace():
    session = parse_session(MEAS2)
    assert session.cwd == "/workspace/project0"


def test_measurement2_detect_format_json_and_jsonl():
    assert detect_format(MEAS2) == "v2"
    assert detect_format(MEAS2.with_suffix(".jsonl")) == "v2"


def test_measurement2_fixture_has_no_home_path():
    text = MEAS2.read_text(encoding="utf-8")
    for needle in ("sirenamc", "/private/tmp", "claude-501", "measure"):
        assert needle not in text, f"home information leaked: {needle}"


# --- edge fixtures ----------------------------------------------------------

def test_empty_metering_usage_is_unpriced():
    session = parse_session(EDGE / "v2_nocredit" / "nocredit.json")
    assert session.engine == "v2"
    turn = _only_turn(session)
    assert turn.state == "closed"
    assert turn.cost is None
    assert turn.unpriced_reason == "empty metering_usage"
    # Requests still counted from total_request_count.
    assert turn.requests == 3


def test_two_turns_sum_only_credit_units():
    session = parse_session(EDGE / "v2_twoturn" / "twoturn.json")
    assert session.engine == "v2"
    assert len(session.turns) == 2
    first, second = session.turns
    assert first.turn_id == "turn-0"
    assert second.turn_id == "turn-1"
    assert round(first.cost, 3) == 0.300
    assert first.requests == 2
    # Second turn has a non-credit token entry that must be ignored.
    assert round(second.cost, 3) == 0.500
    assert second.requests == 4
    # Per-turn start is derived from that turn's own duration.
    assert (first.end - first.start).total_seconds() == 10.0
    assert (second.end - second.start).total_seconds() == 20.0


def test_v2_parser_never_raises_on_missing_path(tmp_path):
    session = parse_session(tmp_path / "does_not_exist.json")
    assert session.engine is None
