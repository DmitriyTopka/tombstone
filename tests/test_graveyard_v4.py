"""Task 15.2: v4 graveyard guarantees across whole pages (R2.10).

test_graveyard_shapes.py covers single stones and the glow band; these tests
check rows as rendered, stability, row CSS and the page-wide hygiene rules.
"""

from __future__ import annotations

import re

from tombstone.tombstones import _g_stone, render_graveyard

from .test_graveyard import _record


def _rows(page: str) -> list:
    """Class lists of the headstones, one list per rendered row."""
    out = []
    for block in re.findall(r'<div class="row"[^>]*>(.*?)</div>', page, re.S):
        out.append(re.findall(r'<button type="button" class="([^"]*)"', block))
    return [r for r in out if r]


def _mixed_records() -> list:
    recs = []
    for i in range(9):
        recs.append(_record(id=f"k/{i}", task_id="unknown", outcome="killed", actual=5.0 + 17 * i))
    for i in range(9):
        recs.append(_record(id=f"u/{i}", task_id="unknown", actual=3.0 + 11 * i))
    for i in range(9):
        recs.append(_record(id=f"p/{i}", task_id=f"3.{i}", actual=2.0 + 13 * i))
    for i in range(9):
        recs.append(_record(id=f"q/{i}", task_id=f"4.{i}", actual=120.0 - 9 * i))
    return recs


def _css(page: str) -> str:
    return re.search(r"<style>(.*?)</style>", page, re.S).group(1)


def _tall_records() -> list:
    """Costs within 12 percent of the peak or more, so no cross becomes a slab.

    A cross under 170 px is swapped for a slab, which can equal a slab
    neighbour; that case is outside what 15.1 guarantees.
    """
    recs = []
    for i in range(9):
        recs.append(_record(id=f"k/{i}", task_id="unknown", outcome="killed", actual=40.0 + 7 * i))
    for i in range(9):
        recs.append(_record(id=f"u/{i}", task_id="unknown", actual=30.0 + 8 * i))
    for i in range(9):
        recs.append(_record(id=f"p/{i}", task_id=f"3.{i}", actual=50.0 + 5 * i))
    for i in range(9):
        recs.append(_record(id=f"q/{i}", task_id=f"4.{i}", actual=100.0 - 4 * i))
    return recs


def test_neighbours_differ_in_every_row():
    _check_neighbours(render_graveyard(_tall_records()), tall=True)
    _check_neighbours(render_graveyard(_mixed_records()), tall=False)


def test_many_cost_mixes_never_match():
    for n in range(2, 14):
        recs = [_record(id=f"m/{i}", task_id="unknown", outcome="killed" if n % 2 else "finished",
                        actual=0.5 + (i * 37 % 11) * (60 if i % 3 else 1)) for i in range(n)]
        _check_neighbours(render_graveyard(recs), tall=False)


def test_note_copy_counts():
    two = [_record(id="a", outcome="killed"), _record(id="b", outcome="killed")]
    assert "All 2 are live" in render_graveyard(two, None, {"a": "x.json", "b": "x.json"})
    assert "1 of them are live" in render_graveyard(two, None, {"a": "x.json"})


def _check_neighbours(page: str, tall: bool) -> None:
    rows = _rows(page)
    assert len(rows) >= 1
    if tall:
        assert min(int(h) for h in re.findall(r"--hh:(\d+)px", page)) >= 170
    for cls in rows:
        shapes = [re.search(r"shape-(\w+)", c).group(1) for c in cls]
        assert all(a != b for a, b in zip(shapes, shapes[1:])), shapes
        cracks = [re.search(r"crack-(\d)", c).group(1) if "crack-" in c else None for c in cls]
        assert all(a != b for a, b in zip(cracks, cracks[1:]) if a is not None), cracks


def test_no_short_cross_anywhere():
    page = render_graveyard(_mixed_records())
    found = re.findall(r'class="(stone [^"]*)" style="--hh:(\d+)px', page)
    assert found
    for cls, h in found:
        if int(h) < 170:
            assert "shape-cross" not in cls, (cls, h)
    for key in ("1", "2", "unmarked", "stopped", ""):
        for i in range(10):
            assert "shape-cross" not in _g_stone(_record(actual=0.01), 100.0, i, key)


def test_shape_and_crack_are_stable():
    recs = _mixed_records()
    first = _rows(render_graveyard(recs))
    second = _rows(render_graveyard([dict(r) for r in recs]))
    assert first == second
    assert _g_stone(_record(), 10.0, 3, "7") == _g_stone(_record(), 10.0, 3, "7")


def test_no_ghost_markup_or_css():
    assert "ghost" not in render_graveyard(_mixed_records()).lower()


def test_lede_and_note_copy_in_page():
    page = render_graveyard([_record(outcome="killed")], None, {"spec/1": "live_stops.json"})
    assert "It is a live test run on a scratch project, kept in live_stops.json." in page
    assert "live_stops.json" in page
    assert "a live test run on a scratch project" not in render_graveyard([_record(outcome="killed")])


def test_stopped_row_caps_eight_and_keeps_gap():
    css = _css(render_graveyard(_mixed_records()))
    css = css[css.index("/* v4: more room"):].split("@media")[0]  # v4 desktop rules
    css_all = _css(render_graveyard(_mixed_records()))
    merged = ";".join(re.findall(r"\.stopped \.row\{([^}]*)\}", css))
    gap = re.findall(r"gap:(\d+)px (\d+)px", merged)[-1]
    row_gap, col_gap = int(gap[0]), int(gap[1])
    assert row_gap >= 80 and col_gap >= 80
    max_w = int(re.findall(r"max-width:(\d+)px", merged)[-1])
    pad = 2 * 18  # base .row padding, left and right
    stone_min = int(re.search(r"\.stone\{[^}]*min-width:(\d+)px", css_all).group(1))

    def fits(n: int) -> bool:
        return n * stone_min + (n - 1) * col_gap <= max_w - pad

    assert fits(8)
    assert not fits(9)
    assert ".stopped .row{flex-wrap:wrap" in css_all


def test_page_has_no_external_resource_or_dash():
    page = render_graveyard(_mixed_records(), None, {"k/0": "live_stops.json"})
    page_no_ns = page.replace("xmlns='http://www.w3.org/2000/svg'", "")  # namespace, never fetched
    for bad in ("http://", "https://", "@import", "<link", "<img", 'src="//'):
        assert bad not in page_no_ns
    assert not re.search(r"url\((?!['\"]?(data:|%23|#))", page)
    assert "\u2014" not in page and "\u2013" not in page
