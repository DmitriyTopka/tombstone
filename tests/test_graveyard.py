"""Task 5: the static graveyard page (R2.6, R4.4)."""

from __future__ import annotations

import json
import re

from tombstone import cli
from tombstone.tombstones import render_graveyard, write_graveyard


def _record(**over) -> dict:
    base = {
        "id": "spec/1", "task_id": "1", "name": "Parse sessions", "spec": "spec",
        "task_type": "other", "engine": "v3", "model": "auto", "requests": 12,
        "estimate": 0.54, "actual": 0.5, "minutes": 3.2, "outcome": "finished",
        "false_positive": None, "date": "2026-11-05", "turns": [],
    }
    base.update(over)
    return base


def test_no_external_resources():
    page = render_graveyard([_record(), _record(id="spec/2", task_id="2")])
    assert "http://" not in page and "https://" not in page
    assert "<link" not in page
    assert not re.search(r"<script[^>]*\ssrc=", page)
    assert "@import" not in page
    assert "url(" not in page
    assert "<style>" in page and "<script>" in page


def test_no_em_or_en_dash():
    page = render_graveyard([_record(name="a - b")])
    assert "\u2014" not in page and "\u2013" not in page


def test_price_is_big_number_with_label():
    page = render_graveyard([_record(actual=0.5, estimate=0.54)])
    assert '<div class="cost">0.500</div>' in page
    assert "credits actual" in page
    assert "est. 0.540" in page


def test_estimate_only_record_is_labeled_est():
    page = render_graveyard([_record(actual=None, estimate=1.25, minutes=None)])
    assert '<div class="cost">1.250</div>' in page
    assert "credits est." in page
    assert "actual pending" in page
    assert "req (est.)" in page


def test_unpriced_record_shows_na():
    page = render_graveyard([_record(actual=None, estimate=None, requests=None, minutes=None)])
    assert '<div class="cost">n/a</div>' in page
    assert "credits unpriced" in page


def test_outcome_badges_and_classes():
    page = render_graveyard([
        _record(id="a", outcome="finished"),
        _record(id="b", outcome="killed"),
        _record(id="c", outcome="cap", actual=0.0, estimate=0.0),
    ])
    assert "FINISHED" in page and "KILLED" in page and "BLOCKED BY CAP" in page
    assert 'class="grave killed"' in page
    assert 'class="grave cap"' in page


def test_sorted_by_cost_descending_before_js():
    page = render_graveyard([
        _record(name="cheap", actual=0.1),
        _record(name="dear", actual=9.0),
        _record(name="mid", actual=2.0),
    ])
    assert page.index("dear") < page.index("mid") < page.index("cheap")


def test_html_is_escaped():
    page = render_graveyard([_record(name='<script>alert("x")</script>', spec="<b>")])
    assert "<script>alert" not in page
    assert "&lt;script&gt;alert" in page
    assert page.count("<script>") == 1


def test_tolerates_bad_records():
    page = render_graveyard(["junk", None, {}, {"actual": "x", "requests": True}])
    assert "<html" in page
    assert "(unnamed)" in page


def test_empty_list_renders_page():
    page = render_graveyard([])
    assert "No tombstones yet" in page
    assert "<b>0</b>" in page


def test_stats_label_estimates_and_actuals():
    page = render_graveyard([
        _record(actual=1.0), _record(actual=3.0), _record(actual=None, estimate=0.5),
        _record(outcome="killed"),
    ])
    assert "credits actual (3 priced)" in page
    assert "credits est. (1 not priced yet)" in page
    assert "killed by the breaker" in page


def test_filter_and_sort_controls_present():
    page = render_graveyard([_record()])
    assert 'id="filter"' in page
    assert 'data-sort="cost"' in page and 'data-sort="date"' in page


def test_write_graveyard_default_path_from_real_shape(tmp_path):
    (tmp_path / ".kiro").mkdir()
    store = {"version": 1, "tombstones": [_record(name="Real one")]}
    source = tmp_path / "tombstones.json"
    source.write_text(json.dumps(store), encoding="utf-8")
    before = source.read_text(encoding="utf-8")

    target = write_graveyard(tmp_path)

    assert target == tmp_path / ".kiro" / "tombstone" / "graveyard.html"
    assert "Real one" in target.read_text(encoding="utf-8")
    assert source.read_text(encoding="utf-8") == before


def test_write_graveyard_without_store(tmp_path):
    target = write_graveyard(tmp_path, tmp_path / "out" / "g.html")
    assert "No tombstones yet" in target.read_text(encoding="utf-8")


def test_cli_graveyard_default_and_out(tmp_path, monkeypatch, capsys):
    (tmp_path / ".kiro").mkdir()
    (tmp_path / "tombstones.json").write_text(
        json.dumps({"version": 1, "tombstones": [_record()]}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["graveyard"]) == 0
    assert (tmp_path / ".kiro" / "tombstone" / "graveyard.html").is_file()
    assert "1 tombstones" in capsys.readouterr().out

    custom = tmp_path / "custom.html"
    assert cli.main(["graveyard", "--out", str(custom)]) == 0
    assert "Parse sessions" in custom.read_text(encoding="utf-8")


def test_cli_graveyard_no_project(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["graveyard"]) == 1
    assert "no project found" in capsys.readouterr().err
