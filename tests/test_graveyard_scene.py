"""Task 28.3: the photographic scene and the rising headstone (R17)."""

from __future__ import annotations

import re
from importlib import resources

from tombstone import demo
from tombstone.tombstones import _SCENE_CREDIT, render_graveyard

_FILES = ("scene_desktop.webp", "scene_mobile.webp", "headstone.webp")


def _page() -> str:
    return render_graveyard([])


def _css(page: str) -> str:
    return page.split("<style>", 1)[1].split("</style>", 1)[0]


def test_package_data_resolves_through_the_package():
    base = resources.files("tombstone").joinpath("data", "scene")
    for name in _FILES:
        assert base.joinpath(name).read_bytes()[:4] == b"RIFF", name


def test_three_data_uris_and_nothing_outside_the_page():
    page = _page()
    assert page.count("data:image/webp;base64,") == 3
    assert "--scene-d:url(" in page and "--scene-m:url(" in page
    assert re.search(r'<img class="hs" alt="" src="data:image/webp;base64,', page)
    for url in re.findall(r"url\(([^)]{0,40})", page):
        assert url.lstrip("\"'").startswith(("data:", "%23", "#")), url
    for src in re.findall(r"""\\bsrc\\s*=\\s*["']([^"']+)""", page):
        assert src.startswith("data:"), src
    assert "https://" not in page and "http://" not in page.replace(
        "http://www.w3.org/2000/svg", "")


def test_page_stays_under_700_kb():
    assert len(_page().encode("utf-8")) < 700 * 1024


def test_credit_line_is_at_the_bottom():
    page = _page()
    line = "Scene and headstone images generated with Nano Banana Pro (Google Gemini)."
    assert _SCENE_CREDIT == line and line in page
    assert page.index(line) > page.index("</footer>")
    assert page.index(line) < page.index("<dialog")


def test_drawn_scenery_is_gone_and_fog_stays():
    page = _page()
    for gone in ("moon-g", 'class="tree', 'class="sil', "l-back", "l-front", "pickets"):
        assert gone not in page, gone
    assert page.count('class="fogl ') == 3 and "@keyframes drift" in page


def test_headstone_height_at_the_stop_matches_the_r15_scale():
    page = _page()
    trace = demo.trace()
    row = next(r for r in trace["caps"] if r["cap"] == trace["default_cap"])
    expected = round(70 + 150 * min(row["figure"] / trace["total"], 1))
    assert f'style="--rh:{expected}px"' in page
    assert "Math.round(70+150*Math.min(c/T.total,1))" in page
    assert 'class="hstone crack-1"' in page and 'class="scrack"' in page
    assert 'id="run-stone-cost"' in page and "credits est." in page


def test_motion_phone_and_noscript_rules():
    page = _page()
    css = _css(page)
    assert "@media (prefers-reduced-motion:reduce)" in css
    assert ".hero .hstone,.hero .hs" in css.replace("\n", "") or ".hero .hstone" in css
    assert "max-width:767px" in css and "--k:.74" in css
    assert "(hover:hover) and (pointer:fine) and (min-width:768px)" in page
    assert 'e.pointerType==="touch"' in page and "*-24" in page
    assert "<noscript>" in page and "0.75 credits" in page
    assert "if(reduce||!earth){return;}" in page
