"""Task 14.1: ``tombstone graveyard --include FILE`` (R2.9)."""

from __future__ import annotations

import json
import re

from tombstone import cli
from tombstone.tombstones import render_graveyard

from .test_graveyard import _record

KICKER = "the graveyard of its own build and its live tests"


def _store(path, *records):
    path.write_text(json.dumps({"version": 1, "tombstones": list(records)}), encoding="utf-8")


def _project(tmp_path, monkeypatch, *records):
    (tmp_path / ".kiro").mkdir()
    _store(tmp_path / "tombstones.json", *records)
    monkeypatch.chdir(tmp_path)
    return tmp_path / ".tombstone" / "graveyard.html"


def _killed(**over):
    base = dict(id="unknown/k1", task_id="unknown", name="Burned turn", outcome="killed")
    base.update(over)
    return _record(**base)


def test_include_merges_and_project_wins(tmp_path, monkeypatch, capsys):
    page = _project(tmp_path, monkeypatch, _record(name="Mine"))
    extra = tmp_path / "live_stops.json"
    _store(extra, _record(name="Theirs"), _killed())
    before = (tmp_path / "tombstones.json").read_bytes()

    assert cli.main(["graveyard", "--include", str(extra)]) == 0
    html = page.read_text(encoding="utf-8")
    assert "Mine" in html and "Theirs" not in html and "Burned turn" in html
    assert "2 tombstones" in capsys.readouterr().out
    assert (tmp_path / "tombstones.json").read_bytes() == before


def test_include_is_repeatable_first_file_wins(tmp_path, monkeypatch):
    page = _project(tmp_path, monkeypatch)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _store(a, _killed(name="From A"))
    _store(b, _killed(name="From B"), _killed(id="unknown/k2", name="Only B"))

    assert cli.main(["graveyard", "--include", str(a), "--include", str(b)]) == 0
    html = page.read_text(encoding="utf-8")
    assert "From A" in html and "From B" not in html and "Only B" in html
    assert "All 2 are live test runs on scratch projects, kept in a.json, b.json." in html


def test_missing_and_bad_include_exit_1_without_page(tmp_path, monkeypatch, capsys):
    page = _project(tmp_path, monkeypatch, _record())
    assert cli.main(["graveyard", "--include", str(tmp_path / "nope.json")]) == 1
    assert "not found" in capsys.readouterr().err
    assert not page.exists()

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert cli.main(["graveyard", "--include", str(bad)]) == 1
    assert "not a tombstones file" in capsys.readouterr().err

    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"version": 1}), encoding="utf-8")
    assert cli.main(["graveyard", "--include", str(wrong)]) == 1
    assert not page.exists()


def test_note_and_kicker_only_with_included_stopped():
    plain = render_graveyard([_killed()])
    assert KICKER not in plain and "live test runs" not in plain

    live = render_graveyard([_record(), _killed()], None, {"unknown/k1": "live_stops.json"})
    assert KICKER in live
    assert "It is a live test run on a scratch project, kept in live_stops.json." in live

    # An included finished record changes the kicker but adds no stopped sentence.
    done = render_graveyard([_record()], None, {"spec/1": "x.json"})
    assert KICKER in done and "live test runs" not in done


def test_killed_reads_stopped_in_card_meta_and_register():
    html = render_graveyard([_killed(), _record(id="spec/2", outcome="blocked")])
    assert 'data-outcome="killed"' in html
    meta = re.search(r'data-meta="([^"]*)"', html).group(1)
    assert "stopped" in meta and "killed" not in meta
    cells = re.findall(r"<td>([^<]*)</td>", html)
    assert "stopped" in cells and "killed" not in cells
    assert "blocked" in cells


def test_repo_live_stops_render_fifteen_stopped_records(tmp_path, monkeypatch):
    from pathlib import Path

    live = Path(__file__).resolve().parent.parent / "live_stops.json"
    page = _project(tmp_path, monkeypatch)

    assert cli.main(["graveyard", "--include", str(live)]) == 0
    html = page.read_text(encoding="utf-8")
    assert html.count('data-outcome="killed"') == 15
    assert "All 15 are live test runs on scratch projects, kept in live_stops.json." in html
    assert KICKER in html
    assert "15 stopped by the breaker" in re.sub(r"<[^>]+>", " ", html).replace("\n", " ") or \
        "<dt>stopped by the breaker</dt><dd>15</dd>" in html
