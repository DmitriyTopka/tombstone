"""Task 11.2: table tests for evaluate boundaries (R4.1, R5.2, R5.3)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.test_watcher_evaluate import CFG, T0, iso, obs, state_with_turn
from tombstone import watcher


def _state(price: float = 0.5, **turn) -> dict:
    state = state_with_turn(**turn)
    state["avg_unit_price"] = price
    return state


# units, prior, expected kind. Cap is 40.0, unit price 0.5.
CAP_TABLE = [
    (0, 0.0, None),
    (10, 0.0, None),
    (80, 0.0, None),          # est. 40.0 equals the cap: not over
    (81, 0.0, "over_cap"),    # est. 40.5
    (60, 10.0, None),         # 30 + 10 equals the cap
    (60, 10.5, "over_cap"),   # 30 + 10.5
    (0, 41.0, "over_cap"),    # prior cost alone is enough
]


@pytest.mark.parametrize("units,prior,kind", CAP_TABLE)
def test_over_cap_table(units, prior, kind):
    event = watcher.evaluate(_state(), obs(units=units, prior=prior), CFG,
                             T0 + timedelta(seconds=1))
    assert (event.kind if event else None) == kind


# quiet seconds, pulse, expected kind. Window is 10 minutes.
STALL_TABLE = [
    (599, True, None),
    (600, True, None),        # exactly the window: not a stall
    (601, True, "stall"),
    (3600, True, "stall"),
    (3600, False, None),      # no pulse, never a stall
]


@pytest.mark.parametrize("quiet,pulse,kind", STALL_TABLE)
def test_stall_table(quiet, pulse, kind):
    now = T0 + timedelta(seconds=quiet)
    # started_at is T0, so the quiet clock starts there. Pulse means the
    # session grew in the last 60 seconds; otherwise growth is long past.
    grew = now if pulse else T0 - timedelta(hours=5)
    state = _state(last_growth_at=iso(grew))
    event = watcher.evaluate(state, obs(units=1), CFG, now)
    assert (event.kind if event else None) == kind


def test_over_cap_wins_over_stall():
    now = T0 + timedelta(hours=1)
    state = _state(last_growth_at=iso(now))
    event = watcher.evaluate(state, obs(units=200), CFG, now)
    assert event is not None and event.kind == "over_cap"


def test_event_already_raised_gives_nothing():
    state = _state(event_raised=True)
    assert watcher.evaluate(state, obs(units=500), CFG, T0) is None
