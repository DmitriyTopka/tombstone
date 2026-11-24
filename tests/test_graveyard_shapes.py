"""Task 15.1: headstone shapes, cracks, no ghost, glow and copy (R2.10)."""

from __future__ import annotations

import re
import zlib

from tombstone.tombstones import _G_SHAPES, _g_stone, render_graveyard

from .test_graveyard import _record


def _classes(page: str) -> list:
    return re.findall(r'<button type="button" class="([^"]*)"', page)


def test_shape_follows_index_and_row_key():
    for key in ("1", "unmarked", "stopped"):
        for i in range(7):
            html = _g_stone(_record(actual=40.0), 40.0, i, key)
            expect = _G_SHAPES[(i + zlib.crc32(key.encode())) % 5]
            assert f"stone shape-{expect}" in html or expect == "cross"


def test_neighbours_never_share_shape_or_crack():
    recs = [_record(id=f"k/{i}", task_id="unknown", outcome="killed", actual=50.0 + i)
            for i in range(10)]
    page = render_graveyard(recs)
    cls = _classes(page)
    assert len(cls) == 10
    shapes = [re.search(r"shape-(\w+)", c).group(1) for c in cls]
    cracks = [re.search(r"crack-(\d)", c).group(1) for c in cls]
    assert all(a != b for a, b in zip(shapes, shapes[1:]))
    assert all(a != b for a, b in zip(cracks, cracks[1:]))
    assert cracks[:4] == ["1", "0", "3", "2"]


def test_cross_becomes_slab_under_170px_and_finished_have_no_crack():
    peak = 100.0
    short = _g_stone(_record(actual=1.0), peak, 2, "")
    tall = _g_stone(_record(actual=100.0), peak, 2, "")
    assert "stone shape-slab" in short and "shape-cross" not in short
    assert "stone shape-cross" in tall
    assert "crack-" not in tall


def test_no_ghost_and_glow_band_for_stopped():
    page = render_graveyard([_record(outcome="killed"), _record(id="spec/2", task_id="2")])
    assert "ghost" not in page
    assert "var(--band)" in page
    assert ".stopped .stone{--band:" in page
    for k in range(4):
        assert f".stopped .stone.crack-{k}{{" in page


def test_copy_all_versus_some():
    recs = [_record(id="a", outcome="killed"), _record(id="b", task_id="2", outcome="killed")]
    some = render_graveyard(recs, None, {"a": "x.json"})
    assert "1 of them are live test runs" in some
    everyone = render_graveyard(recs, None, {"a": "x.json", "b": "x.json"})
    assert "All 2 are live test runs" in everyone
    assert "or a task the breaker stopped. It is as tall as its price." in everyone
