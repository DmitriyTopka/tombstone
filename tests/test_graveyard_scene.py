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


def test_data_uris_and_nothing_outside_the_page():
    page = _page()
    # two background photos, plus the headstone image once per half (R18.4)
    assert page.count("data:image/webp;base64,") == 4
    assert "--scene-d:url(" in page and "--scene-m:url(" in page
    assert re.search(r'<img class="hs" alt="" src="data:image/webp;base64,', page)
    for url in re.findall(r"url\(([^)]{0,40})", page):
        assert url.lstrip("\"'").startswith(("data:", "%23", "#")), url
    for src in re.findall(r"""\\bsrc\\s*=\\s*["']([^"']+)""", page):
        assert src.startswith("data:"), src
    assert "https://" not in page and "http://" not in page.replace(
        "http://www.w3.org/2000/svg", "")


def test_page_stays_under_750_kb():
    assert len(_page().encode("utf-8")) < 750 * 1024


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
    assert f'style="--rh:{expected}px;' in page
    assert "Math.round(70+150*Math.min(c/T.total,1))" in page
    assert 'class="hstone crack-1"' in page and 'class="scrack"' not in page
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


def test_carved_text_fits_inside_the_blank_face():
    css = _css(_page()).replace("\n", "")
    carve = re.search(r"\.hero \.hstone \.carve\{[^}]*?left:(\d+)%;right:(\d+)%", css)
    assert carve
    width = 100 - int(carve.group(1)) - int(carve.group(2))
    face = 80  # blank face is about 80 percent of the stone image width
    assert width <= 0.7 * face and "container-type:inline-size" in css
    cost = float(re.search(r"\.hero \.hstone \.cost\{[^}]*?font-size:([\d.]+)cqw", css).group(1))
    unit = float(re.search(r"\.hero \.hstone \.cunit\{[^}]*?font-size:([\d.]+)cqw", css).group(1))
    assert cost * 5 * 0.65 <= 100  # "12.34" in bold serif, about 0.65 em per glyph
    assert unit * 12 * 0.85 <= 100  # "CREDITS EST." with letter spacing
    assert unit < cost
    assert 'class="cunit"' in _page()


def test_no_line_stroke_or_glow_in_the_gap_after_the_split():
    page = _page()
    css = _css(page)
    for gone in ("scrack", 'class="glow"', 'class="cut"', "crackpulse", "crackrun"):
        assert gone not in page, gone
    assert "scrack" not in css and "_CRACK_D" not in page
    assert 'class="half hl"' in page and 'class="half hr"' in page


def test_earth_is_hidden_at_rest_and_builds_with_the_run():
    page = _page()
    css = _css(page).replace("\n", "")
    assert ".hero .gsvg .mound,.hero .gsvg .heap,.hero .gsvg .scr,.hero .gsvg .tear,.hero .gsvg .tufts,.hero .soil .lump{opacity:0}" in css
    assert ".hero .earth.on .gsvg .heap" in css and ".hero .earth.on .gsvg .mound{opacity:1}" in css
    assert ".hero .earth.on.heave .gsvg .heap" in css
    js = _script(page).replace("\n", "")
    reset = js.split("function reset(){", 1)[1].split("function finish", 1)[0]
    assert '"heave","dig","on"' in reset
    assert 'earth.classList.add("on","heave")' in js
    # noscript and reduced-motion final state: the heap is on in the server render
    assert 'class="earth on" id="run-earth"' in page


# --- Task 29: the headstone breaks through the earth (R18) -------------------

def _script(page: str) -> str:
    return page.split("RUN_DATA=", 1)[1].split("</script>", 1)[0]


def test_earth_elements_exist_in_the_hero():
    page = _page()
    hero = page.split('<header class="hero', 1)[1].split("</header>", 1)[0]
    for cls in ('class="mound"', 'class="scr"', 'class="heap"', 'class="tear"',
                'class="tufts"', 'class="shed"'):
        assert cls in hero, cls
    assert len(re.findall(r'class="clump fall"', hero)) >= 8
    assert len(re.findall(r'class="clump lump"', hero)) >= 4
    assert 'class="dust"' in hero and 'id="soilf"' in hero and "feDiffuseLighting" in hero
    assert re.search(r'id="run-earth" style="--rh:\d+px;--hp:[\d.]+"', hero)
    css = _css(page).replace("\n", "")
    assert ".hero .earth.heave .mound{animation:swell" in css
    assert ".hero .earth.dig .fall{animation:shed" in css
    assert ".hero .gsvg .heap{transform:scale(" in css


def test_heap_grows_with_the_final_height():
    css = _css(_page()).replace("\n", "")
    assert "calc(.05 + .95 * var(--hp,1))" in css
    assert '(hh-70)/150' in _script(_page())


def test_split_uses_two_halves_of_the_same_headstone_image():
    page = _page()
    imgs = re.findall(r'<img class="hs" alt="" src="(data:image/webp;base64,[^"]+)"', page)
    assert len(imgs) == 2 and imgs[0] == imgs[1]
    assert page.count('class="half hl"') == 1 and page.count('class="half hr"') == 1
    css = _css(page).replace("\n", "")
    assert ".hero.stopped .hstone .hl{clip-path:polygon(var(--cl))" in css
    assert ".hero.stopped .hstone .hr{clip-path:polygon(var(--cr))" in css
    cl = re.search(r"--cl:([^;\"]+)", page).group(1)
    assert cl.startswith("0% 0%,") and cl.split(",")[1].strip().endswith("% 0.0%") and cl.endswith("0% 100%")


def test_stop_and_split_come_after_the_last_rise_step():
    js = _script(_page()).replace("\n", "")
    assert "SETTLE=450" in js
    assert "if(i>=steps){timer=setTimeout(function(){finish(r);},SETTLE);return;}" in js
    assert 'earth.classList.add("on","heave")' in js and "},HEAVE);" in js
    finish = js.split("function finish(r){", 1)[1].split("function run()", 1)[0]
    assert 'line("stop","STOP")' in finish and 'hero.classList.add("scare")' in finish
    # the stop and the split are never reached from inside the rise loop
    tick = js.split("function tick(){", 1)[1].split("if(earth){earth.classList.add", 1)[0]
    assert "finish(r)" in tick and "STOP" not in tick and "scare" not in tick


def test_reduced_motion_and_noscript_show_the_split_stone_in_its_heap():
    page = _page()
    css = _css(page).replace("\n", "")
    assert '<header class="hero stopped"' in page and 'class="runner stopped"' in page
    assert ".hero.stopped .hstone .hl{clip-path" in css
    reduce = css.split("@media (prefers-reduced-motion:reduce){  .hero .fogl", 1)[1].split("@media (max-width:900px)", 1)[0]
    assert ".hero .hstone .half" in reduce and ".hero .shed{display:none}" in reduce
    assert "heave" not in reduce
    assert "--hp:" in page.split('id="run-earth"', 1)[1][:60]


# --- Task 29.5: nothing on the path at rest (R17.2, R18.1) -------------------

def test_at_rest_no_band_heap_or_stone_is_visible_and_the_stone_is_clipped():
    page = _page()
    css = _css(page).replace("\n", "")
    hero = page.split('<header class="hero', 1)[1].split("</header>", 1)[0]
    # no drawn soil band across the path
    assert 'class="ground"' not in hero and 'fill="#5b3f2b" filter="url(#soilf)"/><path d="M0 46.5' not in hero
    # at rest (no .on) the stone sits fully below the ground line
    assert ".hero .earth:not(.on) .hstone{top:calc(228px * var(--k))" in css
    # the underground part is clipped by the overflow box, not covered by a drawn element
    assert re.search(r"\.hero \.earth\{[^}]*overflow:hidden", css)
    # mound, heap, cracks, torn edge, tufts and lumps are invisible until the run starts
    base = ".hero .gsvg .mound,.hero .gsvg .heap,.hero .gsvg .scr,.hero .gsvg .tear,.hero .gsvg .tufts,.hero .soil .lump{opacity:0}"
    assert base in css
    assert ".hero .earth.on .gsvg .tufts" in css
    # reset returns to rest, and the run adds the soil from the heave
    js = _script(page).replace("\n", "")
    assert '"heave","dig","on"' in js.split("function reset(){", 1)[1].split("function finish", 1)[0]
