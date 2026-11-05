"""Tests for the CLI entry point and ``tombstone cost`` (task 2)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tombstone import cli

FIXTURES = Path(__file__).parent / "fixtures"
MEAS5_DIR = FIXTURES / "v3_measurement5" / "sess_8b5bdb0f-3662-4dca-be63-268a0365f3d9"
MEAS5_ID = "8b5bdb0f-3662-4dca-be63-268a0365f3d9"
EDGE = FIXTURES / "edge"


# --- acceptance: measurement 5 -------------------------------------------


def test_cost_on_v3_fixture_path(capsys):
    rc = cli.main(["cost", str(MEAS5_DIR)])
    out = capsys.readouterr().out
    assert rc == 0
    # Acceptance: 0.390 credits, 5 requests.
    assert "0.390 credits (actual)" in out
    assert "5 requests (actual)" in out
    assert "total: 0.390 credits (actual), 5 requests" in out


def test_cost_on_fixture_messages_jsonl(capsys):
    rc = cli.main(["cost", str(MEAS5_DIR / "messages.jsonl")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "0.390 credits (actual)" in out


def test_cost_line_has_engine_model_and_times(capsys):
    cli.main(["cost", str(MEAS5_DIR)])
    out = capsys.readouterr().out
    assert "v3" in out
    assert "auto" in out
    assert "start 2026-11-05T17:37:56" in out
    assert "end 2026-11-05T17:38:11" in out


# --- id and prefix resolution against a fake kiro_home --------------------


def _fake_v3_home(tmp_path: Path) -> Path:
    """A minimal kiro_home holding a copy of the measurement 5 V3 session."""
    home = tmp_path / ".kiro"
    dest = home / "sessions" / "14e984af5081f39b" / MEAS5_DIR.name
    dest.parent.mkdir(parents=True)
    shutil.copytree(MEAS5_DIR, dest)
    return home


def test_cost_by_full_id(tmp_path, capsys):
    home = _fake_v3_home(tmp_path)
    rc = cli.main(["cost", MEAS5_ID, "--kiro-home", str(home)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "0.390 credits (actual)" in out
    assert "5 requests (actual)" in out


def test_cost_by_unique_id_prefix(tmp_path, capsys):
    home = _fake_v3_home(tmp_path)
    rc = cli.main(["cost", "8b5bdb0f", "--kiro-home", str(home)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "0.390 credits (actual)" in out


def test_cost_by_sess_prefixed_id(tmp_path, capsys):
    home = _fake_v3_home(tmp_path)
    rc = cli.main(["cost", "sess_" + MEAS5_ID, "--kiro-home", str(home)])
    assert rc == 0
    assert "0.390 credits (actual)" in capsys.readouterr().out


def test_cost_ambiguous_prefix_reports_and_fails(tmp_path, capsys):
    home = tmp_path / ".kiro"
    root = home / "sessions" / "hash"
    for suffix in ("aaaa1111", "aaaa2222"):
        dest = root / f"sess_{suffix}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(MEAS5_DIR, dest)
    rc = cli.main(["cost", "aaaa", "--kiro-home", str(home)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ambiguous id prefix" in err
    assert "aaaa1111" in err and "aaaa2222" in err


def test_cost_unknown_id_exits_1(tmp_path, capsys):
    home = _fake_v3_home(tmp_path)
    rc = cli.main(["cost", "doesnotexist", "--kiro-home", str(home)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no session found" in err


# --- edge fixtures: formatting and exit codes -----------------------------


def test_cost_open_turn_prints_est(capsys):
    rc = cli.main(["cost", str(EDGE / "open" / "sess_open")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "(est.)" in out
    assert "2 requests (est.)" in out


def test_cost_unpriced_turn_prints_reason(capsys):
    rc = cli.main(["cost", str(EDGE / "nocredit" / "sess_nocredit")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "unpriced: no credit entry in usage_summary" in out


def test_cost_empty_session_exits_1(capsys):
    rc = cli.main(["cost", str(EDGE / "empty" / "sess_empty")])
    captured = capsys.readouterr()
    assert rc == 1
    assert "no turns" in captured.err


def test_cost_unknown_format_path_exits_1(capsys):
    rc = cli.main(["cost", str(EDGE / "unknown" / "notasession.txt")])
    captured = capsys.readouterr()
    assert rc == 1
    assert "unknown format" in captured.err


def test_cost_missing_argument_exits_1(tmp_path, capsys):
    rc = cli.main(["cost", str(tmp_path / "nope"), "--kiro-home", str(tmp_path)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "no session found" in captured.err


# --- numbers always carry a basis label -----------------------------------


def test_every_credit_number_has_at_least_three_decimals(capsys):
    cli.main(["cost", str(MEAS5_DIR)])
    out = capsys.readouterr().out
    for line in out.splitlines():
        if "credits" in line:
            # The figure before "credits" has three or more decimal places.
            figure = line.split("credits")[0].strip().split()[-1]
            assert "." in figure
            assert len(figure.split(".")[1]) >= 3


# --- parser wiring ---------------------------------------------------------


def test_no_command_prints_help_exit_2(capsys):
    rc = cli.main([])
    assert rc == 2


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "tombstone" in capsys.readouterr().out


# --- project root discovery -------------------------------------------------


def test_find_project_root_detects_kiro_dir(tmp_path):
    (tmp_path / ".kiro").mkdir()
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert cli.find_project_root(deep) == tmp_path.resolve()


def test_find_project_root_detects_toml(tmp_path):
    (tmp_path / "tombstone.toml").write_text("[caps]\n", encoding="utf-8")
    assert cli.find_project_root(tmp_path) == tmp_path.resolve()


def test_find_project_root_none_when_no_marker(tmp_path):
    assert cli.find_project_root(tmp_path) is None
