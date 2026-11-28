"""Task 37.2: the first screen explains its own numbers (R26)."""

from __future__ import annotations

import re

from tombstone.tombstones import render_graveyard

from .test_graveyard import _record

LINE = ("Tombstone stops at the first step that goes over the cap, "
        "so STOP reads a little above it.")
LEGEND = ("est. is Tombstone's own running estimate of the turn; live is the credit "
          "counter Kiro CLI writes while the turn runs. The watcher stops on whichever "
          "is higher.")


def _page() -> str:
    return render_graveyard([_record()])


def test_stop_line_under_both_cap_controls_and_in_noscript():
    page = _page()
    hero = re.search(r'<header class="hero.*?</header>', page, re.S).group(0)
    bury = re.search(r'<section class="bury".*?</section>', page, re.S).group(0)
    assert hero.count(LINE) == 1 and hero.index("</div>\n", hero.index('id="run-go"')) < hero.index(LINE)
    assert bury.count(LINE) == 2  # once under the controls, once inside noscript
    assert bury.index("bury-play") < bury.rindex(LINE)
    noscript = re.search(r"<noscript>(?:(?!</noscript>).)*</noscript>", bury, re.S).group(0)
    assert LINE in noscript
    assert ".bury .capnote{font-size:16px" in page and ".runner .capnote{margin:-6px 0 14px;font-size:16px" in page


def test_stop_panel_names_the_cap():
    page = _page()
    assert re.search(r"<p>First step over the \d+\.\d\d cap\.</p>", page)
    assert 'line("","First step over the "+f(r.cap)+" cap.")' in page


def test_legend_and_accessible_descriptions():
    page = _page()
    assert f'id="run-legend">{LEGEND.replace(chr(39), "&#x27;")}</span>' in page
    assert 'aria-describedby="run-legend"' in page
    assert page.count(f'title="{LEGEND.replace(chr(39), "&#x27;")}"') >= 2


def test_caption_has_a_solid_backing():
    page = _page()
    rule = re.search(r"\.hero \.runner \.caption\{[^}]*\}", page).group(0)
    m = re.search(r"background:rgba\(4,6,14,(\.\d+)\)", rule)
    assert m and float(m.group(1)) >= .9
    assert "z-index:8" in rule and "color:#d4dbe6" in rule
    assert re.search(r"\.bury \.caption\{background:#080b14", page)


def test_page_stays_one_file_without_network():
    page = _page()
    assert len(page.encode("utf-8")) < 1_500_000
    for word in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "@import"):
        assert word not in page
    assert "\u2014" not in page and "\u2013" not in page


def test_hero_legend_stays_inside_its_container_on_a_backing():
    page = _page()
    rule = re.search(r"\.hero \.runner \.legend\{([^}]*)\}", page).group(1)
    assert "max-width:min(30em,100%)" in rule
    assert "background:rgba(4,6,14,.94)" in rule and "padding:" in rule
    assert "left:0" in rule and "overflow-wrap:anywhere" in rule
    assert not re.search(r"(?:margin|left|translate)[^;]*:\s*-\d", rule)
    assert "max-width:100%" in re.search(r"\.hero \.runner \.meter\{max-width[^}]*\}", page).group(0)
