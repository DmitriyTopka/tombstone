"""Task 27.3: the hero first screen (R16)."""

from __future__ import annotations

import re

from tombstone.tombstones import render_graveyard

from .test_graveyard import _record


def _page(**kw) -> str:
    return render_graveyard([_record()], **kw)


def _hero(page: str) -> str:
    return re.search(r'<header class="hero.*?</header>', page, re.S).group(0)


def test_hero_copy_and_controls():
    hero = _hero(_page())
    assert "Tombstone stops a Kiro agent when a task goes over its budget." in hero
    assert "<h1>Set a cap. <em>Release the agent.</em></h1>" in hero
    assert 'id="run-cap"' in hero and 'type="range"' in hero
    assert ">Release the agent</button>" in hero
    assert "A recorded real Kiro CLI session, scrubbed." in hero
    assert 'id="run-sound"' in hero and 'aria-pressed="false">Sound off<' in hero
    assert ">STOP</p>" in hero and "hook exit code 2" in hero and "Without the breaker" in hero


def test_scene_layers_are_inline_svg():
    hero = _hero(_page())
    for needle in ('id="halo"', 'class="moon-g"', 'class="fogl f1"', 'class="fogl f2"',
                   'class="fogl f3"', 'class="tree', 'class="sil', "<svg"):
        assert needle in hero, needle


def test_no_second_run_block_and_past_section_follows():
    page = _page()
    assert page.count('id="runner"') == 1 and page.count('id="run-go"') == 1
    hero_end = page.index("</header>")
    heading = page.index("The graveyard of this build")
    assert hero_end < heading < page.index("<main>")
    after = page[heading:]
    assert "<h3 class=\"total\">" in after and 'class="proof"' in after
    assert "Click a cracked stone" not in page  # no stopped stone in this fixture
    assert "Each stone is a task" in after


def test_links_in_hero_and_past_section():
    page = _page(video="https://v.test/a", repo="https://r.test/b")
    assert page.count('href="https://v.test/a"') == 2
    assert 'href="https://r.test/b"' in _hero(page)


def test_no_audio_element_and_no_external_hosts():
    page = _page(video="https://v.test/a", repo="https://r.test/b")
    assert "<audio" not in page and "Audio(" not in page
    urls = re.findall(r"""(?:src|href)\s*=\s*["']([^"']+)""", page)
    assert sorted(urls) == ["https://r.test/b", "https://r.test/b",
                            "https://v.test/a", "https://v.test/a"]
    assert "AudioContext" in page


def test_reduced_motion_noscript_and_phone_rules():
    page = _page()
    css = page.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "@media (prefers-reduced-motion:no-preference)" in css
    assert ".hero::after{display:none}" in css
    assert "max-width:640px" in css and ".t-extra,.sil-extra{display:none}" in css
    hero = _hero(page)
    assert "<noscript>" in hero and "0.75 credits" in hero
    assert 'class="runner stopped"' in hero and "crack-1" in hero
    assert "var reduce" in page and "if(!reduce){hero.classList.add(\"scare\")" in page


def test_no_dash_characters_and_deterministic():
    page = _page()
    assert "\u2014" not in page and "\u2013" not in page
    assert page == _page()
