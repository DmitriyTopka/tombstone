"""Task 5: the static graveyard page (R2.6, R4.4)."""

from __future__ import annotations

import json
import re

from tombstone import cli
from tombstone.tombstones import read_plot_names, render_graveyard, write_graveyard


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
    page = render_graveyard([_record(), _record(id="spec/2", task_id="2", outcome="killed")])
    # The only allowed URL is the xmlns of the inline SVG data URI.
    assert "https://" not in page
    assert page.replace("http://www.w3.org/2000/svg", "").count("http://") == 0
    assert "<link" not in page
    assert not re.search(r"<script[^>]*\ssrc=", page)
    assert "@import" not in page
    assert "<style>" in page and "<script>" in page


def test_no_em_or_en_dash():
    page = render_graveyard([_record(name="a - b")])
    assert "\u2014" not in page and "\u2013" not in page


def test_headstone_is_in_html_source_with_label():
    page = render_graveyard([_record(actual=0.5, estimate=0.54)])
    assert '<span class="cost">0.500</span>' in page
    assert '<span class="unit">credits actual</span>' in page
    assert "Parse sessions" in page
    assert "0.540 credits est." in page


def test_estimate_only_record_is_labeled_est():
    page = render_graveyard([_record(actual=None, estimate=1.25, minutes=None)])
    assert '<span class="cost">1.250</span>' in page
    assert '<span class="unit">credits est.</span>' in page
    assert "n/a, actual pending" in page


def test_unpriced_record_shows_na():
    page = render_graveyard([_record(actual=None, estimate=None, requests=None, minutes=None)])
    assert '<span class="cost">n/a</span>' in page
    assert "credits unpriced" in page


def test_stopped_row_and_unmarked_row():
    page = render_graveyard([
        _record(id="a", outcome="finished"),
        _record(id="b", task_id="2", outcome="killed"),
        _record(id="c", task_id="3", outcome="cap", actual=0.0, estimate=0.0),
        _record(id="d", task_id="unknown", name="hello there"),
    ])
    assert "Stopped by the breaker" in page
    assert "Unmarked tasks" in page
    stopped = page.index("Stopped by the breaker")
    finished = page.index("Finished tasks")
    assert stopped < finished
    assert page.index("hello there") > page.index("Unmarked tasks")
    assert 'class="ghost"' not in page


def test_plot_names_and_grouping():
    page = render_graveyard(
        [_record(task_id="2.1", name="x"), _record(task_id="2.2", name="y"),
         _record(task_id="7", name="z")],
        {"2": "Writer"})
    assert "<b>Writer</b>" in page
    assert "<b>Task 7</b>" in page
    assert "2 tasks" in page and "1 task," in page


def test_height_grows_with_cost():
    page = render_graveyard([_record(name="cheap", actual=0.1), _record(name="dear", actual=9.0)])
    heights = {n: int(h) for h, n in re.findall(r'--h:(\d+)px"[^>]*data-title="(\w+)"', page)}
    assert heights["dear"] > heights["cheap"]
    assert heights["dear"] == 268


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


def test_reduced_motion_dialog_and_register():
    page = render_graveyard([_record()])
    assert "prefers-reduced-motion" in page
    assert '<dialog id="card"' in page
    assert 'id="tb"' in page and 'data-k="actual"' in page and 'id="q"' in page


def test_read_plot_names(tmp_path):
    spec = tmp_path / ".kiro" / "specs" / "s"
    spec.mkdir(parents=True)
    (spec / "tasks.md").write_text(
        "- [x] 1. Parser for `V3`\n  - [ ] 1.1 sub\n- [ ] 2. Writer\n", encoding="utf-8")
    assert read_plot_names(tmp_path) == {"1": "Parser for V3", "2": "Writer"}
    assert read_plot_names(tmp_path / "none") == {}


def test_write_graveyard_uses_plot_names(tmp_path):
    spec = tmp_path / ".kiro" / "specs" / "s"
    spec.mkdir(parents=True)
    (spec / "tasks.md").write_text("- [ ] 1. Session parser\n", encoding="utf-8")
    (tmp_path / "tombstones.json").write_text(
        json.dumps({"version": 1, "tombstones": [_record(task_id="1.1")]}), encoding="utf-8")
    assert "<b>Session parser</b>" in write_graveyard(tmp_path).read_text(encoding="utf-8")


def test_write_graveyard_default_path_from_real_shape(tmp_path):
    (tmp_path / ".kiro").mkdir()
    store = {"version": 1, "tombstones": [_record(name="Real one")]}
    source = tmp_path / "tombstones.json"
    source.write_text(json.dumps(store), encoding="utf-8")
    before = source.read_text(encoding="utf-8")

    target = write_graveyard(tmp_path)

    assert target == tmp_path / ".tombstone" / "graveyard.html"
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
    assert (tmp_path / ".tombstone" / "graveyard.html").is_file()
    assert "1 tombstones" in capsys.readouterr().out

    custom = tmp_path / "custom.html"
    assert cli.main(["graveyard", "--out", str(custom)]) == 0
    assert "Parse sessions" in custom.read_text(encoding="utf-8")


def test_cli_graveyard_no_project(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["graveyard"]) == 1
    assert "no project found" in capsys.readouterr().err


def _strip_script(page: str) -> str:
    page = page.split("<main>", 1)[-1]  # the header strip repeats names
    return re.sub(r"<script>.*?</script>", "", page, flags=re.S)


def test_plot_grouping_from_tasks_fixture(tmp_path):
    spec = tmp_path / ".kiro" / "specs" / "s"
    spec.mkdir(parents=True)
    (spec / "tasks.md").write_text(
        "- [x] 1. Parser for `V3`\n  - [x] 1.1 a\n- [ ] 2. Writer\n", encoding="utf-8")
    records = [
        _record(id="s/1.1", task_id="1.1", name="alpha"),
        _record(id="s/1.2", task_id="1.2", name="beta"),
        _record(id="s/2", task_id="2", name="gamma"),
        _record(id="s/9", task_id="9", name="delta"),
    ]
    (tmp_path / "tombstones.json").write_text(
        json.dumps({"version": 1, "tombstones": records}), encoding="utf-8")
    page = _strip_script(write_graveyard(tmp_path).read_text(encoding="utf-8"))
    assert "<b>Parser for V3</b>" in page
    assert "<b>Writer</b>" in page
    assert "<b>Task 9</b>" in page
    assert page.index("alpha") < page.index("beta") < page.index("<b>Parser for V3</b>")
    assert page.index("<b>Parser for V3</b>") < page.index("gamma") < page.index("<b>Writer</b>")


def test_unknown_records_only_in_unmarked_graves():
    page = _strip_script(render_graveyard([
        _record(id="u/1", task_id="unknown", name="orphan one"),
        _record(id="s/3", task_id="3", name="plotted"),
    ]))
    unmarked = page.index("Unmarked tasks")
    assert page.index("orphan one") > unmarked
    assert page.index("plotted") < unmarked
    assert "<b>Task unknown</b>" not in page


def test_killed_and_blocked_in_stopped_row_not_in_plots():
    page = _strip_script(render_graveyard([
        _record(id="a", task_id="1", name="fine", outcome="finished"),
        _record(id="b", task_id="2", name="was killed", outcome="killed"),
        _record(id="c", task_id="3", name="was blocked", outcome="blocked"),
    ]))
    stopped = page.index("Stopped by the breaker")
    finished = page.index("Finished tasks")
    assert stopped < page.index("was killed") < finished
    assert stopped < page.index("was blocked") < finished
    assert page.index("fine") > finished
    assert 'class="ghost"' not in page


def test_headstones_present_without_javascript():
    page = _strip_script(render_graveyard([_record(name="visible stone", actual=2.5)]))
    assert "<script" not in page
    assert "visible stone" in page
    assert '<span class="cost">2.500</span>' in page
    assert "--h:" in page


def _lede(page: str) -> str:
    return re.search(r'<p class="lede" id="lede">(.*?)</p>', page).group(1)


V3_LEDE = ("Each stone is a spec task Kiro finished or a task the breaker stopped, "
           "as tall as its price. "
           "Open a stone for the estimate, the requests and the time.")


def _h1(page: str) -> str:
    return re.search(r"<h1>(.*?)</h1>", page).group(1)


def _strip(page: str) -> dict:
    block = re.search(r'<dl class="proof">(.*?)</dl>', page, re.S)
    assert block, "no proof strip"
    return {dt: (dd, cls.strip()) for cls, dt, dd in re.findall(
        r"<div([^>]*)><dt>(.*?)</dt><dd>(.*?)</dd></div>", block.group(1))}


def test_v3_header_copy_title_and_hills():
    page = render_graveyard([_record(actual=0.5)])
    assert '<p class="kicker">Tombstone, the record of its own build</p>' in page
    assert _lede(page) == V3_LEDE
    assert "<title>The Graveyard, 0.50 credits</title>" in page
    assert '<svg class="hills"' in page and 'class="stars"' in page
    assert "--night:#060912" in page


def test_v3_headline_total_is_sum_of_stone_costs():
    records = [_record(actual=0.5), _record(id="b", task_id="2", actual=None, estimate=1.25),
               _record(id="c", task_id="3", actual=None, estimate=None)]
    page = render_graveyard(records)
    shown = sum(float(c) for c in re.findall(r'<span class="cost">([\d.]+)</span>', page))
    assert _h1(page) == f"{shown:.2f} credits are buried here."
    assert _h1(page) == "1.75 credits are buried here."
    assert "<title>The Graveyard, 1.75 credits</title>" in page


def test_v3_proof_strip_figures():
    page = render_graveyard([
        _record(id="a", task_id="1", name="Big One", actual=4.0, requests=10),
        _record(id="b", task_id="2", name="small", actual=1.0, requests=5),
        _record(id="c", task_id="3", name="mid", actual=2.0, requests=7),
        _record(id="d", task_id="4", name="est", actual=None, estimate=3.0, requests=None),
    ])
    strip = _strip(page)
    assert list(strip) == ["tasks buried", "stopped by the breaker", "the most expensive task"]
    assert strip["tasks buried"][0] == "4"
    assert strip["the most expensive task"][0] == "4.00"
    assert strip["stopped by the breaker"][0] == "0"


def test_v3_stopped_figure_only_with_stopped_records_and_is_red():
    page = render_graveyard([_record(), _record(id="b", task_id="2", outcome="killed"),
                             _record(id="c", task_id="3", outcome="blocked")])
    assert _strip(page)["stopped by the breaker"] == ("2", "class=\"red\"")
    assert 'class="red"' not in render_graveyard([_record()])


def test_v3_empty_has_no_strip():
    page = render_graveyard([])
    assert _h1(page) == "Nothing is buried here yet."
    assert "proof" in page  # the css rule only
    assert "<dl class=\"proof\"" not in page
    assert "No tombstones yet" in page
    assert "<title>The Graveyard, 0.00 credits</title>" in page


def test_v3_header_escapes_names():
    page = render_graveyard([_record(name="<i>x</i>", actual=1.0)])
    assert "<i>x</i>" not in page.split("<main>")[0]
    assert "the most expensive task" in page
    assert "&lt;i&gt;x&lt;/i&gt;" not in page.split("<main>")[0]


def test_v3_mobile_rule_keeps_stone_text_readable():
    page = render_graveyard([_record()])
    mobile = page[page.index("@media (max-width:640px)"):page.index("</style>")]
    assert ".stone .tid,.stone .unit,.unmarked .stone .tid{font-size:14px}" in mobile


def test_v3_mobile_block_has_no_small_px_font():
    page = render_graveyard([_record()])
    mobile = page[page.index("@media (max-width:640px)"):page.index("</style>")]
    sizes = [int(n) for n in re.findall(r"font-size:(\d+)px", mobile)]
    assert sizes and min(sizes) >= 14
