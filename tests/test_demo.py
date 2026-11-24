"""Tests for ``tombstone demo`` (task 22.2, R14). No Kiro, no network."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from tombstone import cli, demo


def _env(home: Path, path: str) -> dict:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = path
    return env


def test_demo_trips_the_breaker(tmp_path, capsys, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    scratch = tmp_path / "scratch"
    assert cli.main(["demo", "--dir", str(scratch)]) == 0
    out = capsys.readouterr().out
    assert "STOP: TOMBSTONE" in out
    assert "hook exit code: 2" in out
    assert "refusal text: TOMBSTONE" in out
    assert "tombstone graveyard --project" in out
    assert (scratch / ".tombstone" / "STOP").is_file()
    assert len(list((scratch / ".tombstone" / "autopsy").glob("*.md"))) == 1
    assert "task = 0.30" in (scratch / "tombstone.toml").read_text()
    assert list(home.iterdir()) == []


def test_demo_subprocess_without_kiro_cli_and_with_empty_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    scratch = tmp_path / "scratch"
    src = Path(demo.__file__).resolve().parent.parent
    env = _env(home, "/nonexistent")
    env["PYTHONPATH"] = str(src)
    result = subprocess.run(
        [sys.executable, "-m", "tombstone", "demo", "--dir", str(scratch)],
        env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "hook exit code: 2" in result.stdout
    assert list(home.iterdir()) == []


def test_demo_default_uses_a_new_temp_dir(capsys):
    assert cli.main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "hook exit code: 2" in out
    line = [x for x in out.splitlines() if x.startswith("receipt: ")][0]
    receipt = Path(line[len("receipt: "):])
    assert receipt.is_file()
    shutil.rmtree(receipt.parents[2], ignore_errors=True)


def test_demo_refuses_a_non_empty_dir(tmp_path, capsys):
    (tmp_path / "keep.txt").write_text("mine")
    assert cli.main(["demo", "--dir", str(tmp_path)]) == 1
    assert "not empty" in capsys.readouterr().err
    assert (tmp_path / "keep.txt").read_text() == "mine"
    assert not (tmp_path / ".tombstone").exists()


def test_packaged_session_has_no_home_path():
    session = demo.packaged_session()
    assert session is not None
    for path in session.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert "/Users/" not in text and "/home/" not in text


def test_package_data_is_declared():
    root = Path(demo.__file__).resolve().parent.parent.parent
    assert "data/**/*" in (root / "pyproject.toml").read_text()
