"""Tests for config loading and similar-task math (task 4.1)."""

from __future__ import annotations

from pathlib import Path

from tombstone import caps
from tombstone.caps import (
    DEFAULT_AVG_REQUEST_PRICE,
    DEFAULT_KILL_GRACE_SECONDS,
    DEFAULT_MIN_PLAN_BALANCE,
    DEFAULT_QUIET_WINDOW_MINUTES,
    DEFAULT_SPEC_CAP,
    DEFAULT_TASK_CAP,
    Config,
    keywords,
    load_config,
    median_similar,
    similar,
    task_type,
)


# --- load_config ------------------------------------------------------------


def test_load_config_missing_file_uses_defaults(tmp_path):
    config = load_config(tmp_path)
    assert config.task_cap == DEFAULT_TASK_CAP
    assert config.spec_cap == DEFAULT_SPEC_CAP
    assert config.quiet_window_minutes == DEFAULT_QUIET_WINDOW_MINUTES
    assert config.avg_request_price == DEFAULT_AVG_REQUEST_PRICE
    assert config.min_plan_balance == DEFAULT_MIN_PLAN_BALANCE
    assert config.kill_grace_seconds == DEFAULT_KILL_GRACE_SECONDS
    assert config.warnings == ()


def test_load_config_reads_every_key(tmp_path):
    (tmp_path / "tombstone.toml").write_text(
        "\n".join(
            [
                "[caps]",
                "task = 50",
                "spec = 400",
                "[stall]",
                "quiet_window_minutes = 15",
                "[budget]",
                "avg_request_price = 0.06",
                "min_plan_balance = 10",
                "[stop]",
                "kill_grace_seconds = 45",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config.task_cap == 50.0
    assert config.spec_cap == 400.0
    assert config.quiet_window_minutes == 15.0
    assert config.avg_request_price == 0.06
    assert config.min_plan_balance == 10.0
    assert config.kill_grace_seconds == 45.0
    assert config.warnings == ()


def test_load_config_partial_file_fills_rest_with_defaults(tmp_path):
    (tmp_path / "tombstone.toml").write_text(
        "[caps]\ntask = 25\n", encoding="utf-8"
    )
    config = load_config(tmp_path)
    assert config.task_cap == 25.0
    # Everything else falls back to the default without a warning.
    assert config.spec_cap == DEFAULT_SPEC_CAP
    assert config.warnings == ()


def test_load_config_bad_value_falls_back_with_warning(tmp_path):
    (tmp_path / "tombstone.toml").write_text(
        '[caps]\ntask = "lots"\nspec = 200\n', encoding="utf-8"
    )
    config = load_config(tmp_path)
    assert config.task_cap == DEFAULT_TASK_CAP
    assert config.spec_cap == 200.0
    assert any("caps.task" in warning for warning in config.warnings)


def test_load_config_boolean_is_not_a_number(tmp_path):
    (tmp_path / "tombstone.toml").write_text(
        "[budget]\nmin_plan_balance = true\n", encoding="utf-8"
    )
    config = load_config(tmp_path)
    assert config.min_plan_balance == DEFAULT_MIN_PLAN_BALANCE
    assert any("budget.min_plan_balance" in w for w in config.warnings)


def test_load_config_parse_error_uses_defaults_with_one_warning(tmp_path):
    (tmp_path / "tombstone.toml").write_text(
        "this is not = = valid toml [[[", encoding="utf-8"
    )
    config = load_config(tmp_path)
    assert config.task_cap == DEFAULT_TASK_CAP
    assert len(config.warnings) == 1
    assert "parse error" in config.warnings[0]


# --- task_type --------------------------------------------------------------


def test_task_type_classifies_by_first_keyword():
    assert task_type("Write pytest fixtures") == "test"
    assert task_type("Session parser spec") == "test"
    assert task_type("Database schema migration") == "migration"
    assert task_type("Refactor the watcher loop") == "refactor"
    assert task_type("Rename the module") == "refactor"
    assert task_type("Update the README") == "docs"
    assert task_type("Add stall evaluation") == "other"


def test_task_type_handles_empty_name():
    assert task_type("") == "other"


# --- keywords ---------------------------------------------------------------


def test_keywords_drops_short_and_stop_words():
    words = keywords("Add the stall evaluation for a turn")
    # "add", "the", "for" are stop words; "a" is too short.
    assert "stall" in words
    assert "evaluation" in words
    assert "turn" in words
    assert "add" not in words
    assert "the" not in words
    assert "a" not in words


def test_keywords_empty_name_gives_empty_set():
    assert keywords("") == set()


# --- similar ----------------------------------------------------------------


def _record(name, *, task_type_="other", outcome="finished", actual=None,
            estimate=None, spec="tombstone"):
    return {
        "name": name,
        "task_type": task_type_,
        "outcome": outcome,
        "actual": actual,
        "estimate": estimate,
        "spec": spec,
    }


def test_similar_matches_type_and_shared_keyword():
    records = [
        _record("Add stall evaluation", task_type_="other", actual=0.5),
        _record("Write pytest for the parser", task_type_="test", actual=0.3),
        _record("Add stall detector loop", task_type_="other", actual=0.6),
    ]
    matches = similar(records, "Add stall over-cap check")
    names = [record["name"] for record in matches]
    assert "Add stall evaluation" in names
    assert "Add stall detector loop" in names
    # Different type (test) is excluded even though it shares no keyword anyway.
    assert "Write pytest for the parser" not in names


def test_similar_excludes_non_finished():
    records = [
        _record("Add stall evaluation", actual=0.5, outcome="killed"),
        _record("Add stall counter", actual=0.4, outcome="finished"),
    ]
    matches = similar(records, "Add stall check")
    assert len(matches) == 1
    assert matches[0]["name"] == "Add stall counter"


def test_similar_filters_by_spec_when_given():
    records = [
        _record("Add stall evaluation", actual=0.5, spec="tombstone"),
        _record("Add stall evaluation", actual=0.5, spec="other-spec"),
    ]
    matches = similar(records, "Add stall check", spec="tombstone")
    assert len(matches) == 1
    assert matches[0]["spec"] == "tombstone"


def test_similar_recomputes_type_for_records_without_field():
    # A record that never stored task_type still matches by its name.
    record = {"name": "Refactor the loop", "outcome": "finished", "actual": 0.2}
    matches = similar([record], "Refactor the parser")
    assert matches == [record]


def test_similar_empty_when_target_has_no_keywords():
    assert similar([_record("Add stall", actual=0.5)], "the a an") == []


# --- median_similar ---------------------------------------------------------


def test_median_similar_uses_actual_basis():
    records = [
        _record("Add stall evaluation", actual=0.4),
        _record("Add stall detector", actual=0.6),
        _record("Add stall counter", actual=0.5),
    ]
    result = median_similar(records, "Add stall check")
    assert result.value == 0.5
    assert result.count == 3
    assert result.basis == "actual"


def test_median_similar_falls_back_to_estimate_basis():
    records = [
        _record("Add stall evaluation", actual=0.4),
        _record("Add stall detector", actual=None, estimate=0.8),
    ]
    result = median_similar(records, "Add stall check")
    # Median of [0.4, 0.8]. One record used its estimate, so basis is est.
    assert result.value == 0.6
    assert result.count == 2
    assert result.basis == "est."


def test_median_similar_no_match_returns_none():
    records = [_record("Write pytest for parser", task_type_="test", actual=0.3)]
    result = median_similar(records, "Add stall over-cap check")
    assert result.value is None
    assert result.count == 0
    assert result.basis == "est."


def test_median_similar_empty_records():
    result = median_similar([], "Add stall check")
    assert result.value is None
    assert result.count == 0
