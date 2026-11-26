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
    # R19.5: two background photos, stone, heap, bones and 8 clods, each exactly once (R23.1: no hand)
    assert page.count("data:image/webp;base64,") == 13
    assert "--scene-d:url(" in page and "--scene-m:url(" in page
    assert "<img" not in page
    for url in re.findall(r"url\(([^)]{0,40})", page):
        assert url.lstrip("\"'").startswith(("data:", "%23", "#")), url
    for src in re.findall(r"""\\bsrc\\s*=\\s*["']([^"']+)""", page):
        assert src.startswith("data:"), src
    assert "https://" not in page and "http://" not in page.replace(
        "http://www.w3.org/2000/svg", "")


def test_page_stays_under_1_35_mb():
    assert len(_page().encode("utf-8")) < 1350 * 1024


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
    assert "(hover:hover) and (pointer:fine) and (min-width:768px)" not in page  # R22.5: no parallax
    assert "<noscript>" in page and "0.75 credits" in page
    assert "if(reduce||!earth){return;}" in page


def test_carved_text_fits_inside_the_blank_face():
    css = _css(_page()).replace("\n", "")
    carve = re.search(r"\.hero \.hstone \.carve\{[^}]*?left:(\d+)%;right:(\d+)%", css)
    assert carve
    width = 100 - int(carve.group(1)) - int(carve.group(2))
    face = 60  # the sunken panel is about 60 percent of the stone image width (20 to 80)
    assert width <= 0.7 * face + 1e-9 and "container-type:inline-size" in css
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
    assert ".hero .gsvg .mound,.hero .gsvg .scr,.hero .gsvg .tear,.hero .gsvg .tufts,.hero .soil .lump,.hero .soil .heap{opacity:0}" in css
    assert ".hero .earth.on .soil .heap" in css and ".hero .earth.on .gsvg .mound{opacity:1}" in css
    assert ".hero .earth.on.heave .soil .heap" in css
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
    for cls in ('class="mound"', 'class="scr"', 'class="ph heap hb"', 'class="ph heap hf"', 'class="tear"',
                'class="tufts"', 'class="shed"'):
        assert cls in hero, cls
    assert len(re.findall(r'class="ph clump fall ', hero)) >= 8
    assert len(re.findall(r'class="ph clump lump ', hero)) >= 4
    assert 'class="dust"' in hero and 'id="soilf"' in hero and "feDiffuseLighting" in hero
    assert re.search(r'id="run-earth" style="--rh:\d+px;--hp:[\d.]+"', hero)
    css = _css(page).replace("\n", "")
    assert ".hero .earth.heave .mound{animation:swell" in css
    assert ".hero .earth.dig .fall{animation:shed" in css
    assert ".hero .heap{" in css and "transform:scale(calc(.2 + .8 * var(--hp,1)))" in css


def test_heap_grows_with_the_final_height():
    css = _css(_page()).replace("\n", "")
    assert "scale(calc(.2 + .8 * var(--hp,1)))" in css
    assert '(hh-70)/150' in _script(_page())


def test_split_uses_two_halves_of_the_same_headstone_image():
    page = _page()
    assert page.count('<span class="hs"></span>') == 2
    assert page.count("--img-stone:url(") == 1 and "background:var(--img-stone)" in page
    assert page.count('class="half hl"') == 1 and page.count('class="half hr"') == 1
    css = _css(page).replace("\n", "")
    assert ".hero.stopped .hstone .hl{clip-path:polygon(var(--cl))" in css
    assert ".hero.stopped .hstone .hr{clip-path:polygon(var(--cr))" in css
    cl = re.search(r"--cl:([^;\"]+)", page).group(1)
    assert cl.startswith("0% 0%,") and cl.split(",")[1].strip().endswith("% 0.0%") and cl.endswith("0% 100%")


def test_stop_and_split_come_after_the_last_rise_step():
    js = _script(_page()).replace("\n", "")
    assert "SETTLE=1000" in js and "PUSH=900" in js
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
    assert ".hero .hstone .half" in reduce and ".hero .shed{display:none}" in reduce and ".hero .fx{display:none}" in reduce
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
    assert re.search(r"\.hero \.earth\{[^}]*clip-path:inset\(0 -160px 0 -160px\)", css)
    # mound, heap, cracks, torn edge, tufts and lumps are invisible until the run starts
    base = ".hero .gsvg .mound,.hero .gsvg .scr,.hero .gsvg .tear,.hero .gsvg .tufts,.hero .soil .lump,.hero .soil .heap{opacity:0}"
    assert base in css
    assert ".hero .earth.on .gsvg .tufts" in css
    # reset returns to rest, and the run adds the soil from the heave
    js = _script(page).replace("\n", "")
    assert '"heave","dig","on"' in js.split("function reset(){", 1)[1].split("function finish", 1)[0]


# --- Task 29.6: the stone ends at the ground, the heap covers its foot ------

def test_stone_clip_bottom_is_the_ground_line_and_cracks_sit_behind_the_stone():
    page = _page()
    css = _css(page).replace("\n", "")
    # ground line: y=48 in the 70 unit soil box; the earth box is 228 units tall
    ground = 228 - (70 - 48)
    bottom = re.search(r"\.hero \.hstone\{clip-path:inset\(0 -60% calc\(\((\d+)px - var\(--rh\)\) \* var\(--k\)\) -60%\)\}", css)
    assert bottom
    # stone top is (228 - rh), its height 220, so its visible bottom is 448 - rh - inset
    assert 228 + 220 - int(bottom.group(1)) == ground
    assert ".hero .earth:not(.on) .hstone{clip-path:inset(0 0 100% 0)}" in css
    # while the ground swells the clip uses the same unit as the rise, hiding everything below the ground line
    assert ".hero .earth.heave .hstone{clip-path:inset(0 -60% calc(242px * var(--k)) -60%)}" in css
    hero = page.split('<header class="hero', 1)[1].split("</header>", 1)[0]
    back, front = hero.split('<div class="soil back"', 1)[1].split('<div class="soil" ', 1)
    for cls in ('class="scr"', 'class="tear"', 'class="mound"', 'class="tufts"'):
        assert cls in back and cls not in front, cls
    # R19.1: the hollow middle of the heap is behind the stone, the rest in front of it
    assert "heap hf" in front and "heap hb" in back and "heap hf" not in back
    z = {m.group(1): int(m.group(2)) for m in re.finditer(r"\.hero (\.soil\.back|\.hstone|\.soil)\{[^}]*?z-index:(\d+)", css)}
    assert z[".soil.back"] < z[".hstone"] < z[".soil"]


# --- Task 29.7: a real heap and natural soil cracks (R18.1, R18.3) ------------

def test_heap_is_the_photograph_at_the_right_size():
    css = _css(_page()).replace("\n", "")
    # 240 of 147 stone units is about 1.6 stone widths; it starts at 20 percent
    assert "width:calc(var(--k) * 240px)" in css and 1.6 <= 240 / 147 <= 1.8
    assert "scale(calc(.2 + .8 * var(--hp,1)))" in css
    assert "--img-heap:url(" in _page() and "<svg" not in _page().split('class="ph heap hf"', 1)[1][:40]
    assert '(hh-70)/150' in _script(_page())


def test_soil_cracks_are_a_few_irregular_fading_fissures_behind_the_heap():
    from tombstone.tombstones import _SOIL_CRACKS
    assert 3 <= len(_SOIL_CRACKS) <= 5
    lengths = {len(re.findall(r"L", d)) for d, _ in _SOIL_CRACKS}
    widths = {w for _, w in _SOIL_CRACKS}
    assert len(lengths) >= 3 and len(widths) >= 3
    page = _page()
    hero = page.split('<header class="hero', 1)[1].split("</header>", 1)[0]
    back = hero.split('<div class="soil back"', 1)[1].split('<div class="soil" ', 1)[0]
    scr = back.split('<g class="scr">', 1)[1].split("</g>", 1)[0]
    assert scr.count("<path") == len(_SOIL_CRACKS)
    assert 'stroke="url(#fis0)"' in scr
    # each fissure fades to nothing at its far end
    fades = re.findall(r'<linearGradient id="fis\d"[^>]*>.*?</linearGradient>', back)
    assert len(fades) == len(_SOIL_CRACKS) and all('stop-opacity="0"' in f for f in fades)
    # no more identical stitches: every fissure starts at a different angle
    firsts = {tuple(re.findall(r"-?\d+(?:\.\d+)?", d)[2:4]) for d, _ in _SOIL_CRACKS}
    assert len(firsts) == len(_SOIL_CRACKS)
    assert "heap hf" not in back


def test_loose_clods_lie_on_the_grass_outside_the_heap():
    from tombstone.tombstones import _LUMPS
    assert len(_LUMPS) >= 6
    assert any(x < 30 for x, _, _ in _LUMPS) and any(x > 200 for x, _, _ in _LUMPS)
    assert all(y <= 14 for _, y, _ in _LUMPS)


# --- Task 30: photographed earth, a skeletal hand and old bones (R19) --------

_IMAGES = ("headstone", "heap", "bones") + tuple(f"clods/clod_{n}" for n in range(8))


def _raw_b64(name: str) -> str:
    import base64
    raw = resources.files("tombstone").joinpath("data", "scene", *f"{name}.webp".split("/")).read_bytes()
    return base64.b64encode(raw).decode("ascii")


def test_each_image_is_embedded_exactly_once():
    page = _page()
    for name in _IMAGES + ("scene_desktop", "scene_mobile"):
        assert page.count(_raw_b64(name)) == 1, name
    for var in ("img-stone", "img-heap", "img-bones", "clod0", "clod7"):
        assert page.count(f"--{var}:url(") == 1, var


def test_no_svg_heap_or_clump_elements_remain():
    from tombstone import tombstones
    page = _page()
    hero = page.split('<header class="hero', 1)[1].split("</header>", 1)[0]
    assert 'class="heap"' not in hero and "heapg" not in hero and 'id="peb"' not in hero
    assert not hasattr(tombstones, "_g_heap") and not re.search(r'<svg[^>]*>(?:(?!</svg>).)*clump', hero)
    assert "radial-gradient(circle at 68% 24%" not in page
    sizes = set(re.findall(r'class="ph clump (?:fall|lump) (c\d)', hero))
    assert len(sizes) >= 5
    assert len(set(re.findall(r"--s:(\d+)", hero))) >= 4


def test_bones_are_shown_and_toned_to_the_moon():
    page = _page()
    css = _css(page).replace("\n", "")
    # R19.7 replaces the R19.3 fade-in: the bones are part of the cemetery at rest
    bones = re.search(r"\.hero \.bones\{[^}]*\}", css).group(0)
    assert "opacity:0" not in bones
    tone = re.search(r"\.hero \.ph\{[^}]*filter:([^;]*);", css).group(1)
    assert "brightness(.8)" in tone and "saturate(.55)" in tone and "hue-rotate(172deg)" in tone
    assert "linear-gradient(210deg" in css  # highlight from the upper right
    assert 'class="ph bones"' in page
    # the stone's carved figure is centred in the sunken panel (x 20 to 80 percent)
    assert "left:29%;right:29%" in css
    js = _script(page).replace("\n", "")
    finish = js.split("function finish(r){", 1)[1].split("function run()", 1)[0]
    assert 'earth.classList.add("full")' in finish  # bones come with the end of the run


def test_reduced_motion_and_noscript_show_the_split_stone_and_bones():
    page = _page()
    css = _css(page).replace("\n", "")
    assert '<header class="hero stopped"' in page
    reduce = css.split("@media (prefers-reduced-motion:reduce){  .hero .fogl", 1)[1].split("@media (max-width:900px)", 1)[0]
    assert ".hero .bones" in reduce and ".hero .heap" in reduce
    assert 'class="earth on" id="run-earth"' in page


def test_page_is_one_file_with_no_external_requests_and_under_1_35_mb():
    page = _page()
    assert len(page.encode("utf-8")) < 1350 * 1024
    assert not re.search(r"<(?:link|script)[^>]+(?:src|href)=[\"']https?:", page)


# --- Task 30.4: real-world scale and a readable stone (R19.6, R17.3, R19.4) ---

def _units(css: str, selector: str, prop: str) -> float:
    """The unit count of ``prop:calc(var(--k) * Npx)`` in the rule for ``selector``."""
    rule = ";".join(re.findall(r"(?<![\w.:-])" + re.escape(selector) + r"\{([^}]*)\}", css))
    return float(re.search(r"(?:^|;)\s*" + prop + r":calc\(var\(--k\) \* (-?[\d.]+)px\)", rule).group(1))


def test_scale_ratios_follow_real_world_proportions_at_both_widths():
    from tombstone.tombstones import _FALLS, _LUMPS
    css = _css(_page()).replace("\n", "")
    stone_w = _units(css, ".hero .hstone", "width")
    stone_h = _units(css, ".hero .hstone", "height")
    ground = 22  # the ground line is 22 units above the bottom of the 228 unit earth box
    # the unit is the stone image box, so the ratio holds at any --k
    k = {"1280": float(re.search(r"\.hero\{--k:([\d.]+)", css).group(1)),
         "390": float(re.search(r"max-width:640px\)\{\s*\.hero\{--k:([\d.]+)", _css(_page())).group(1))}
    assert k["1280"] > k["390"] > 0
    heap_w = _units(css, ".hero .heap", "width")
    heap_h = _units(css, ".hero .heap", "height")
    heap_bottom = _units(css, ".hero .heap", "bottom")
    above = heap_h - (ground - heap_bottom)
    bones_w = _units(css, ".hero .bones", "width")
    bones_h = _units(css, ".hero .bones", "height")
    earth_w = _units(css, ".hero .earth", "width")
    stone_left = (earth_w - stone_w) / 2
    skull = 0.52  # the skull spans the top 52 percent of bones.webp
    for scale in (k["1280"], k["390"]):
        W, H = stone_w * scale, stone_h * scale
        assert 0.20 <= above * scale / H <= 0.25
        assert 1.6 <= heap_w * scale / W <= 1.8
        # R19.7: about 1.1 times the R19.6 size, they lie closer to the camera
        assert 0.65 * 1.05 <= bones_w * scale / W <= 0.80 * 1.15
        assert 0.15 * 1.05 <= skull * bones_h * scale / H <= 0.20 * 1.15
        for _, _, s in _LUMPS:
            assert 0.04 <= s * 2 / stone_w <= 0.14
        for _, _, s, _, _ in _FALLS:
            assert 0.04 <= s * 2 / stone_w <= 0.14 and s <= min(s2 for _, _, s2 in _LUMPS) + 2


def test_stone_is_mid_grey_with_a_light_edge_and_the_carved_figure_is_readable():
    css = _css(_page()).replace("\n", "")
    tone = re.search(r"\.hero \.hstone \.hs\{[^}]*filter:([^;]*);", css).group(1)
    assert float(re.search(r"brightness\(([\d.]+)\)", tone).group(1)) >= 1.2
    assert "sepia" not in tone and "hue-rotate" not in tone
    assert ".hero .hstone .hs::after" in css and "linear-gradient(255deg" in css  # light right edge
    carve = re.search(r"\.hero \.hstone \.carve\{[^}]*\}", css).group(0)
    # the carve box (left 29 to 71 percent) is centred on the panel (x 20 to 80) and sits at its top (y 31 to 78)
    assert "left:29%;right:29%;top:34%" in carve


# --- Task 30.5: bones to the side, a glowing figure (R19.7, R19.8) ----------

def _stone_html(page: str) -> str:
    return page.split('<div id="run-stone"', 1)[1].split('<div class="soil', 1)[0]


def _rule(css: str, selector: str) -> str:
    return ";".join(re.findall(r"(?<![\w.:-])" + re.escape(selector) + r"\{([^}]*)\}", css))


def test_figure_is_one_element_above_both_stone_halves():
    page = _page()
    css = _css(page).replace("\n", "")
    stone = _stone_html(page)
    # one figure, after both halves and outside them, so the split clips never cut it
    assert stone.count('class="carve"') == 1 and stone.count('class="cost"') == 1
    assert stone.index('class="half hl"') < stone.index('class="half hr"') < stone.index('class="carve"')
    for half in re.findall(r'<div class="half h[lr]"[^>]*>(.*?)</div>', stone):
        assert "carve" not in half and "cost" not in half
    assert 'id="run-stone-cost"' in stone
    half_z = int(re.search(r"z-index:(\d+)", _rule(css, ".hero .hstone .half")).group(1))
    carve_z = int(re.search(r"z-index:(\d+)", _rule(css, ".hero .hstone .carve")).group(1))
    assert carve_z > half_z
    # the split rules clip and move only the halves
    for rule in re.findall(r"\.hero\.stopped \.hstone \.h[lr]\{[^}]*\}", css):
        assert "carve" not in rule
    assert not re.search(r"\.carve[^{]*\{[^}]*clip-path", css)


def test_figure_is_visible_after_the_first_step_and_after_the_split():
    page = _page()
    css = _css(page).replace("\n", "")
    carve = _rule(css, ".hero .hstone .carve")
    assert "opacity:1" in carve and "left:29%;right:29%" in carve  # 42 percent of the stone wide
    cost = _rule(css, ".hero .hstone .cost")
    assert re.search(r"font-size:([\d.]+)cqw", cost) and "display:block" in cost
    # no rule hides the figure or gives it zero size, in any state
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if ".hero" in sel and any(n in sel for n in ("carve", ".cost", "cunit")):
            assert not re.search(r"opacity:0(?![.\d])|display:none|visibility:hidden|font-size:0(?![.\d])", body), sel
    # it sits at the top of the panel (y 34 percent), above the heap front copy from the first step it clears it
    top = float(re.search(r"top:([\d.]+)%", carve).group(1))
    assert 31 <= top <= 40
    # the script writes every step to the figure, and the server renders the stop figure for no-JS
    assert 'id="run-stone-cost">0.80<' in page or re.search(r'id="run-stone-cost">\d+\.\d\d<', page)
    assert 'stone.querySelectorAll(".cost")' in _script(page)


def test_figure_glows_blue_white_flickers_while_rising_and_holds_after_the_split():
    css = _css(_page()).replace("\n", "")
    carve = _rule(css, ".hero .hstone .carve")
    assert "color:#f4f9ff" in carve and "color:#f4f9ff" in _rule(css, ".hero .hstone .cost")
    shadow = re.search(r"text-shadow:([^;}]*)", carve).group(1)
    assert "#fff" in shadow and shadow.count("rgba(") >= 3  # near-white core and a pale blue halo
    assert all(int(b) > int(r) for r, _, b in re.findall(r"rgba\((\d+),(\d+),(\d+)", shadow))
    spill = _rule(css, ".hero .hstone .carve::before")
    assert "radial-gradient" in spill and "rgba(150,195,255" in spill  # faint light on the stone around it
    motion = css.split("@media (prefers-reduced-motion:no-preference){  .hero .runner", 1)[1].split("@media (prefers-reduced-motion:reduce)", 1)[0]
    motion = ".hero .runner" + motion
    assert ".hero .runner:not(.stopped) .hstone .carve{animation:glowflick" in motion
    assert "@keyframes glowflick" in motion
    assert "glowflick" not in css.split("@media (prefers-reduced-motion:no-preference){  .hero .runner", 1)[0]
    assert "glowflick" not in css.split("@keyframes glowflick", 1)[1]  # only while rising, never in the stopped state


def _hero_boxes(width: float) -> dict:
    """Boxes (left, top, right, bottom) in px from the hero's bottom left corner, y up, for the 1280 px layout.

    The earth box is centred in the hero (the central grid column), so its left edge is (width - earth) / 2.
    """
    css = _css(_page()).replace("\n", "")
    k = float(re.search(r"\.hero\{--k:([\d.]+)", css).group(1))
    u = lambda sel, prop: _units(css, sel, prop) * k  # noqa: E731
    earth_w = u(".hero .earth", "width")
    ex = (width - earth_w) / 2
    # the earth box bottom sits at least on the caption line above the hero bottom
    caption = 60.0
    stone_w, stone_h = u(".hero .hstone", "width"), u(".hero .hstone", "height")
    sx = ex + earth_w / 2 + u(".hero .hstone", "margin-left")
    boxes = {
        "stone": (sx, caption, sx + stone_w, caption + stone_h),
        "heap": (ex + u(".hero .heap", "left"), caption + u(".hero .heap", "bottom"),
                 ex + u(".hero .heap", "left") + u(".hero .heap", "width"),
                 caption + u(".hero .heap", "bottom") + u(".hero .heap", "height")),
    }
    bones = _rule(css, ".hero .bones")
    bl = float(re.search(r"left:([\d.]+)%", bones).group(1)) / 100 * width
    bb = float(re.search(r"bottom:([\d.]+)px", bones).group(1))
    boxes["bones"] = (bl, bb, bl + u(".hero .bones", "width"), bb + u(".hero .bones", "height"))
    return boxes


def test_bones_lie_in_the_left_foreground_away_from_stone_and_heap():
    page = _page()
    width = 1280.0
    boxes = _hero_boxes(width)
    bl, bb, br, bt = boxes["bones"]
    assert br <= 0.30 * width and bl >= 0  # the left 30 percent of the hero
    for name in ("stone", "heap"):
        l, b, r, t = boxes[name]
        assert br <= l or bl >= r or bt <= b or bb >= t, name
    # outside the earth box (its clip would cut them) and directly in the hero, visible at rest
    hero = page.split('<header class="hero', 1)[1].split('<div class="hero-inner">', 1)[0]
    assert '<i class="ph bones" aria-hidden="true"></i>' in hero
    assert "bones" not in page.split('id="run-earth"', 1)[1].split('<div class="verdict"', 1)[0]
    assert page.count('class="ph bones"') == 1
    # at 390 px they would overlap the caption, so they are hidden there (R19.7 allows it)
    phone = _css(page).split("@media (max-width:640px){\n  .hero{--k:", 1)[1].split("\n}", 1)[0]
    assert ".hero .bones{display:none}" in phone


# --- Task 31: night ambience behind the sound toggle (R20.1 to R20.3) ---

def _ambience_b64() -> str:
    import base64
    raw = resources.files("tombstone").joinpath("data", "scene", "ambience.m4a").read_bytes()
    return base64.b64encode(raw).decode("ascii")


def test_ambience_is_embedded_once_as_audio_mp4_and_never_autoplays():
    page = _page()
    assert page.count(_ambience_b64()) == 1
    assert page.count("data:audio/mp4;base64,") == 5  # ambience plus four effects
    assert "<audio" not in page and "autoplay" not in page
    assert "<source" not in page


def test_ambience_starts_only_from_the_first_interaction_or_the_toggle():
    script = _page().split("var AMBIENCE=", 1)[1]
    script = script.split(";\n", 1)[1]
    assert script.count("sfxOn()") == 3  # definition, ambOn, and the decode at load (preload)
    # playing is reached only through ambOn: the one-time unlock and the toggle
    assert script.count("ambOn()") == 3  # definition, unlock, toggle
    unlock = script.split("function unlock(){", 1)[1].split("EVS.forEach(function(n){document.add", 1)[0]
    assert "if(soundOn){ambOn();}" in unlock and "if(unlocked){return;}unlocked=true;" in unlock
    handler = script.split('snd.addEventListener("click"', 1)[1]
    assert "if(soundOn){ambOn();}else{ambOff();}" in handler


def test_ambience_loops_with_fades_and_ducks_under_the_crack():
    script = _page().split("var AMBIENCE=", 1)[1]
    assert "src.loop=true" in script
    assert "linearRampToValueAtTime(LEVEL,t+1.5)" in script  # fade in about 1.5 s
    assert "ambStop(a,ac.currentTime,.5)" in script  # fade out about 0.5 s, then stop
    assert "a.src.stop(" in script
    boom = script.split("function boom(){", 1)[1].split("function reset", 1)[0]
    assert "duck();" in boom  # the stop ducks the ambience under the crack and thud
    duck = script.split("function duck(){", 1)[1].split("if(snd)", 1)[0]
    assert "setValueAtTime(LEVEL*.15,t+1)" in duck and "linearRampToValueAtTime(LEVEL,t+" in duck
    # one Web Audio context shared by the crack and the ambience, and the reduced-motion path keeps sound
    assert script.count("new C()") == 1
    assert "prefers-reduced-motion" in script and "if(!reduce){hero.classList.add(\"scare\");}boom();" in script


def test_ambience_credit_line_is_at_the_bottom():
    page = _page()
    assert "Night ambience generated with Suno, sound effects with ElevenLabs." in page
    assert page.count('<p class="credit">') == 1
    assert page.index("Night ambience generated with Suno, sound effects") > page.index("</main>")


# --- Task 32: sound for every movement (R21.1 to R21.3) ---

_SFX = ("ground_break", "stone_slide", "earth_crumble", "stone_split")


def _sfx_b64(name: str) -> str:
    import base64
    raw = resources.files("tombstone").joinpath("data", "scene", "sfx", name + ".m4a").read_bytes()
    return base64.b64encode(raw).decode("ascii")


def _run_script() -> str:
    return _page().split("var AMBIENCE=", 1)[1]


def test_each_effect_is_embedded_exactly_once():
    page = _page()
    for name in _SFX:
        assert page.count(_sfx_b64(name)) == 1, name
        assert page.count('"' + name + '":"data:audio/mp4;base64,') == 1, name
    assert page.count("new C()") == 1  # decoded through the one existing context
    assert page.count("decodeAudioData(") == 2  # one for the ambience, one loop for the effects


def test_effects_are_scheduled_on_their_events():
    s = _run_script()
    assert 'sfx("ground_break",.9,1,0);' in s.split("function run(){", 1)[1].split("function label", 1)[0]
    tick = s.split("function tick(){", 1)[1].split("if(i>=steps)", 1)[0]
    assert "stepSound();" in tick
    step = s.split("function stepSound(){", 1)[1].split("\n", 1)[0]
    assert 'sfx("earth_crumble"' in step and "rnd(" in step and "stone_grind" not in s
    boom = s.split("function boom(){", 1)[1].split("function rnd", 1)[0]
    assert 'sfx("stone_split"' in boom
    assert "duck();" in boom


def test_effects_play_only_with_sound_on_and_duck_the_ambience():
    s = _run_script()
    play = s.split("function sfx(", 1)[1].split("function stepSound", 1)[0]
    assert "if(!soundOn||!ac||!b){return;}" in play and "duck();" in play
    assert "function boom(){if(!soundOn||!ac){return;}" in s
    assert "sfxOn()" in s.split("function ambOn(){", 1)[1].split("\n", 1)[0]


def test_reduced_motion_plays_only_the_split():
    s = _run_script()
    assert 'if(!reduce){hero.classList.add("scare");}boom();' in s
    assert "if(reduce){finish(r);return;}" in s  # no step, heave or slide on that path
    assert "function slideStart(){if(reduce||" in s


def test_synthesized_crack_is_gone():
    s = _run_script()
    assert "createOscillator" not in s and "createBiquadFilter" not in s


def test_effects_credit_line():
    page = _page()
    assert "Night ambience generated with Suno, sound effects with ElevenLabs." in page
    assert page.count('<p class="credit">') == 1


# --- Task 33: music from the start, a violent break-through, no milky haze (R22) ---

def _fog_rules(css: str) -> dict:
    out = {}
    for cls in ("f1", "f2", "f3"):
        rule = re.search(r"\.hero \." + cls + r"\{([^}]*)\}", css).group(1)
        stopped = re.search(r"\.hero\.stopped \." + cls + r"\{([^}]*)\}", css).group(1)
        out[cls] = (rule, stopped)
    return out


def test_sound_defaults_to_on_and_starts_on_the_first_interaction_only():
    page = _page()
    assert 'id="run-sound" type="button" class="snd" aria-pressed="true">Sound on<' in page
    s = _run_script()
    assert "soundOn=true" in s and "soundOn=false" not in s
    assert 'var EVS=["pointerdown","touchstart","keydown"]' in s
    # the listeners are added once, and the unlock removes them
    assert s.count("document.addEventListener(n,unlock,true)") == 1
    assert "document.removeEventListener(n,unlock,true)" in s
    # nothing is started at load: the context is created (suspended) by mkAc and only resumed in wake()
    top = s.split("var EVS=", 1)[0]
    assert "new C()" in top.split("function mkAc(){", 1)[1].split("function wake(){", 1)[0]
    assert s.count("ambOn();") == 2  # the unlock and the toggle, nothing else
    assert top.count("wake();") >= 1
    assert "wake();slideStart();fissures();" in s  # "Release the agent" also wakes a suspended context


def test_effects_of_r21_are_unchanged():
    s = _run_script()
    for name in ("ground_break", "earth_crumble", "stone_split"):
        assert 'sfx("' + name + '"' in s, name


def test_eruption_spawns_15_to_25_clods_and_never_more_than_40_particles():
    s = _run_script()
    assert "BURST_CLODS=[15,25]" in s and "MAXP=40" in s
    tufts, dust = (int(re.search(r"BURST_%s=(\d+)" % n, s).group(1)) for n in ("TUFTS", "DUST"))
    assert 25 + tufts + dust <= 40  # the whole burst fits under the cap
    assert "live.length>=MAXP){return null;}" in s  # every particle goes through spawn()
    assert s.count("document.createElement(\"i\")") == 1
    assert "requestAnimationFrame(frame)" in s
    assert "if(reduce||!fx" in s  # reduced motion: no particles
    assert "erupt();tick();" in s


def test_eruption_has_swells_tremor_fissures_and_wobble():
    page = _page()
    s = _run_script()
    css = _css(page).replace("\n", "")
    assert "HEAVE=1500" in s and "n<3" in s and "shake(2.5," in s  # three swells
    assert "Math.min(3,m)" in s  # tremor of at most 3 px
    assert "strokeDashoffset" in s and "fissures();" in s
    assert "shake(2,.3);rubble();" in s.split("function tick(){", 1)[1].split("if(i>=steps)", 1)[0]
    wob = css.split("@keyframes wobble{", 1)[1].split("}}", 1)[0]
    degs = [abs(float(d)) for d in re.findall(r"rotate\((-?[\d.]+)deg\)", wob)]
    assert degs and max(degs) <= 1.5
    assert ".hero .earth.dig .hstone{animation:wobble" in css
    assert 'id="run-fx"' in page


def test_dust_and_flash_are_never_white():
    css = _css(_page()).replace("\n", "")
    for rule in (".hero .fx .dp{", ".hero .burst{"):
        body = css.split(rule, 1)[1].split("}", 1)[0]
        for r, g, b in re.findall(r"rgba?\((\d+),(\d+),(\d+)", body):
            assert max(int(r), int(g), int(b)) < 100, (rule, r, g, b)
    flash = css.split(".hero::after{content", 1)[1].split("}", 1)[0]
    assert "background:#8a0f12" in flash
    assert re.search(r"@keyframes flash\{0%\{opacity:0\}18%\{opacity:\.3\}", css)


def test_fog_is_low_faint_cool_and_textured():
    page = _page()
    css = _css(page).replace("\n", "")
    base = re.search(r"\.hero \.fogl\{([^}]*)\}", css).group(1)
    height = float(re.search(r"height:([\d.]+)%", base).group(1))
    for cls, (rule, stopped) in _fog_rules(css).items():
        bottom = float(re.search(r"--fb:([\d.]+)%", rule).group(1))
        assert bottom + height <= 100 / 3 + 0.01, cls  # inside the lowest third
        for text in (rule, stopped):
            assert float(re.search(r"--fo:([\d.]+)", text).group(1)) <= 0.18, cls
    fogg = page.split('id="fogg"', 1)[1].split("</radialGradient>", 1)[0]
    for hexa in re.findall(r'stop-color="#(\w{2})(\w{2})(\w{2})"', fogg):
        r, g, b = (int(v, 16) for v in hexa)
        assert max(r, g, b) < 0xB0 and b >= r  # blue-grey, not milky
    assert "feTurbulence" in page.split('id="wisp"', 1)[1].split("</filter>", 1)[0]
    assert page.count('filter="url(#wisp)"') == 3


def test_reduced_motion_shows_the_final_state_without_particles():
    css = _css(_page()).replace("\n", "")
    reduce = css.split("@media (prefers-reduced-motion:reduce){  .hero .fogl", 1)[1].split("@media (max-width:900px)", 1)[0]
    assert ".hero .fx{display:none}" in reduce
    s = _run_script()
    assert "if(reduce){finish(r);return;}" in s
    assert "function shake(m,d){if(reduce){return;}" in s
    assert "wobble" not in reduce and "swell" not in reduce


# --- Task 34: damage vignette, a still camera, no hand, sound bound to motion (R22.4, R22.5, R23) ---

def _js_fn(src: str, name: str) -> str:
    return "function " + name + "(" + src.split("function " + name + "(", 1)[1].split("\nfunction ", 1)[0].split("\n/*", 1)[0]


def _node_eval(expr: str):
    """Evaluate the vignette and slide helpers of the page script in node, or skip without node."""
    import json
    import shutil
    import subprocess

    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    s = _run_script()
    funcs = "\n".join(_js_fn(s, n) for n in ("vigStrength", "vigPeriod", "slideGain"))
    consts = "var VIG_FROM=.4,VIG_BEAT=.8,SLIDE_VMAX=40;"
    out = subprocess.run([node, "-e", consts + funcs + "\nconsole.log(JSON.stringify(" + expr + "))"],
                         capture_output=True, text=True, check=True, timeout=20).stdout
    return json.loads(out)


def test_vignette_strength_is_zero_below_40_percent_and_rises_monotonically():
    qs = [i / 100 for i in range(0, 131)]
    st = _node_eval("[%s].map(function(q){return vigStrength(q,1);})" % ",".join(map(str, qs)))
    assert all(v == 0 for q, v in zip(qs, st) if q <= 0.4)
    rising = [v for q, v in zip(qs, st) if 0.4 <= q <= 1]
    assert all(b > a for a, b in zip(rising, rising[1:]))
    assert abs(max(st) - 1) < 1e-9 and st[100] == 1 and st[130] == 1
    assert st[70] < 0.5 * st[100]  # ease-in: slow at first, strong near the cap
    assert _node_eval("vigStrength(1,0)") == 0  # no division by a zero cap


def test_vignette_beats_from_80_percent_and_faster_near_the_cap():
    per = _node_eval("[.5,.79,.8,.9,1,1.2].map(function(q){return vigPeriod(q,1);})")
    assert per[0] == 0 and per[1] == 0
    assert per[2] > per[3] > per[4] > 0 and per[5] == per[4]
    s = _run_script()
    css = _css(_page()).replace("\n", "")
    assert "heartbeat" in css and ".hero .vig.beat{animation:heartbeat var(--bp" in css
    assert "var VIG_FROM=.4,VIG_BEAT=.8" in s


def test_vignette_peaks_at_the_stop_stays_and_is_absent_when_the_cap_is_not_reached():
    s = _run_script().replace("\n", "")
    finish = s.split("function finish(r){", 1)[1].split("function run()", 1)[0]
    stopped = finish.split("if(steps){", 1)[1].split("else{", 1)[0]
    cleared = finish.split("else{", 1)[1]
    assert "vigHit();" in stopped and "vigClear();" in cleared
    assert 'vig.style.setProperty("--vg","1")' in s.split("function vigHit(){", 1)[1].split("function vigClear", 1)[0]
    # it persists until the next run or a slider move: only reset() clears it, and the slider calls reset()
    assert "vigClear();" in s.split("function reset(){", 1)[1].split("function finish", 1)[0]
    assert 'slider.addEventListener("input",function(){label();reset();});' in s
    assert "vigSet(T.credits[i-1],r.cap,false);" in s.split("function tick(){", 1)[1].split("if(i>=steps)", 1)[0]
    css = _css(_page()).replace("\n", "")
    assert re.search(r"\.hero \.vig\{[^}]*pointer-events:none[^}]*opacity:var\(--vg,0\)", css)
    assert ".hero.stopped .vig{--vg:1}" in css  # the static stopped state keeps it
    assert "@keyframes vighit{0%{opacity:.2}12%{opacity:1" in css
    # deep red at the edges, clear in the centre, never white
    grad = re.search(r"\.hero \.vig\{[^}]*background:(radial-gradient\([^;]*\))", css).group(1)
    assert "rgba(110,0,8,0) 42%" in grad
    for r, g, b in re.findall(r"rgba\((\d+),(\d+),(\d+)", grad):
        assert int(r) > 100 and int(g) < 20 and int(b) < 20
    # reduced motion: no pulsing, no transition; the beat and hit animations live in the no-preference block
    anim = css.split("@media (prefers-reduced-motion:no-preference){  .hero .runner:not(.stopped)", 1)[1].split("@media (prefers-reduced-motion:reduce)", 1)[0]
    assert ".hero .vig.beat{" in anim and ".hero .vig.hit{" in anim
    reduce = css.split("@media (prefers-reduced-motion:reduce){  .hero .fogl", 1)[1].split("@media (max-width:900px)", 1)[0]
    assert "heartbeat" not in reduce and ".hero .vig" in reduce
    assert "vig=document.getElementById(\"run-vig\")" in s and 'id="run-vig"' in _page()


def test_photo_never_moves():
    page = _page()
    css = _css(page).replace("\n", "")
    photo = re.search(r"\.hero \.photo\{([^}]*)\}", css).group(1)
    for bad in ("translate", "transform", "transition", "will-change", "scale", "inset:-"):
        assert bad not in photo, bad
    assert "pointermove" not in page and "pointerleave" not in page
    assert "getBoundingClientRect" not in page.split("var RUN_DATA=", 1)[0]
    s = _run_script()
    assert "querySelector(\".photo\")" not in s and ".photo" not in s
    # the only transform driver is the short tremor on the scene and fog, at most 3 px
    assert "Math.min(3,m)" in s


def test_no_hand_is_embedded_and_no_hand_sounds_remain():
    import base64
    page = _page()
    bare = re.sub(r"data:[A-Za-z0-9/+;=,-]+", "", page)  # without the base64 payloads
    css = _css(bare)
    assert 'class="hand"' not in bare and "hand_reach" not in bare and "img-hand" not in bare
    assert ".hand" not in css and "hup" not in bare and "--hl" not in bare
    sfx = resources.files("tombstone").joinpath("data", "scene", "sfx")
    for name in ("hand_emerge", "hand_sink", "stone_grind"):
        assert name not in page
    assert not sfx.joinpath("hand_emerge.m4a").is_file() and not sfx.joinpath("hand_sink.m4a").is_file()
    assert "function hand(" not in page and "function spray(" not in page
    assert not resources.files("tombstone").joinpath("data", "scene", "hand_reach.webp").is_file()


def test_stone_slide_is_embedded_once_and_stone_grind_is_not():
    page = _page()
    assert page.count(_sfx_b64("stone_slide")) == 1
    assert page.count('"stone_slide":"data:audio/mp4;base64,') == 1
    assert "stone_grind" not in page
    s = _run_script()
    assert s.count("createBufferSource()") == 3  # ambience, one-shot effects, the one slide loop
    assert s.count("src.buffer=sfxBuf.stone_slide;src.loop=true;") == 1
    assert "wake();slideStart();" in s  # started once per run, not once per step
    assert "slideStart" not in s.split("function tick(){", 1)[1].split("if(i>=steps)", 1)[0]
    assert "slideStart" not in s.split("function stepSound(){", 1)[1].split("\n", 1)[0]


def test_slide_gain_follows_the_stone_speed_and_is_zero_when_still():
    gains = _node_eval("[0,-5,.5,1,10,20,40,80].map(function(v){return slideGain(v,1);})")
    assert gains[0] == 0 and gains[1] == 0 and gains[2] == 0 and gains[3] == 0  # still or sliding down: silent
    assert 0 < gains[4] < gains[5] < gains[6] and gains[6] == gains[7] == 1  # louder with speed, capped
    assert _node_eval("slideGain(20,2)") < gains[5]  # speed is scaled by the size of the scene
    s = _run_script().replace("\n", "")
    frame = s.split("function slideFrame(now){", 1)[1].split("function ", 1)[0]
    assert "getComputedStyle(stone).top" in frame and "slideGain(v,k)" in frame
    assert "requestAnimationFrame(slideFrame)" in frame  # every frame
    assert "slide.g.gain.setTargetAtTime(n*SLIDE_VOL,t,.05)" in frame  # about 0.15 s (3 time constants) to silence
    assert "playbackRate.setTargetAtTime(.94+.12*n" in frame
    stop = s.split("function slideStop(){", 1)[1].split("function slideFrame", 1)[0]
    assert "setTargetAtTime(0,t,.05)" in stop and "src.stop(" in stop
    assert "function slideStart(){if(reduce||!soundOn||" in s  # reduced motion and sound off: no movement sound
    toggle = s.split('snd.addEventListener("click"', 1)[1]
    assert "if(!soundOn){slideStop();}" in toggle


def test_each_rise_step_lasts_0_8_to_1_0_s_inside_the_step_interval():
    page = _page()
    css = _css(page).replace("\n", "")
    rule = re.search(r"\.hero \.hstone\{position:absolute;[^}]*transition:top ([\d.]+)s (cubic-bezier\([^)]*\)),clip-path ([\d.]+)s \2", css)
    assert rule, "the stone rises by a timed transition of top and clip-path together"
    dur = float(rule.group(1))
    assert 0.8 <= dur <= 1.0 and float(rule.group(3)) == dur
    x1, y1, x2, y2 = (float(v) for v in re.findall(r"[\d.]+", rule.group(2)))
    assert y1 == 0 and y2 == 1 and 0 < x1 < 1 and 0 < x2 < 1  # ease in and ease out
    s = _run_script().replace("\n", "")
    assert f"PUSH={int(dur * 1000)}" in s
    assert "gap=Math.max(PUSH+200,6000/steps)" in s  # the push fits inside every step interval
    assert "SETTLE=1000" in s and int(re.search(r"SETTLE=(\d+)", s).group(1)) >= int(dur * 1000)
    assert "earth_crumble" in s.split("function stepSound(){", 1)[1].split("\n", 1)[0]  # tied to the clods
    # no step is shorter than the push at the default pace
    from tombstone import demo
    steps = len(demo.trace()["credits"])
    assert max(6000 / steps, dur * 1000 + 200) >= dur * 1000 + 200
    assert steps * (dur + 0.2) <= 10  # a run stays inside the 4 to 10 s of R15.3


def test_effects_decode_at_load_and_the_slide_retries_when_its_buffer_arrives():
    s = _run_script()
    top, after = s.split("EVS.forEach(function(n){document.add", 1)
    # the decode starts at load, before any interaction listener is added
    assert "function preload(){if(!soundOn){return;}mkAc();sfxOn();ambDecode();}\npreload();\n" in top + "\n"
    unlock = top.split("function unlock(){", 1)[1].split("/* R21.1", 1)[0]
    assert "mkAc()" not in unlock and "decodeAudioData" not in unlock
    assert "ac.resume()" in top.split("function wake(){", 1)[1]
    # a missing slide buffer is remembered and started when it arrives
    start = s.split("function slideStart(){", 1)[1].split("function slideStop", 1)[0]
    assert "if(!sfxBuf.stone_slide){slWant=true;return;}" in start
    assert 'if(k==="stone_slide"&&slWant){slideStart();}' in s
    # the pending start is dropped when the run ends or is reset
    assert "slWant=false;" in s.split("function slideStop(){", 1)[1].split("\n", 1)[0]
    assert "if(slide){slEnd=true;}slWant=false;" in s
    # the ambience does not start before the first interaction
    assert "if(unlocked){ambPlay();}" in s


# --- Task 34.6: the vignette covers the whole scene (R22.4) ------------------

def _z(css: str, selector: str) -> int:
    return int(re.search(r"z-index:(-?\d+)", _rule(css, selector)).group(1))


def test_vignette_is_above_every_scene_layer_and_below_the_text_and_controls():
    css = _css(_page())
    vig = _z(css, ".hero .vig")
    # bones and every other scene layer sit below the vignette
    for sel in (".hero .photo", ".hero .shade", ".hero .bones", ".hero .fx", ".hero .runner .grave",
                ".hero .f2", ".hero .f3", ".hero .burst", ".hero .shed"):
        assert _z(css, sel) < vig, sel
    assert _z(css, ".hero .bones") < vig
    # the wrappers of the stone must not trap it in a stacking context below or above the vignette
    for sel in (".hero-inner", ".hero .runner"):
        assert "z-index" not in _rule(css, sel), sel
    assert "translate" not in _rule(css, ".hero .runner .scene")
    # headline, controls, counter, verdict (STOP, refusal) and caption sit above it
    for sel in (".hero h1", ".hero .tagline", ".hero .runner .controls", ".hero .runner .meter",
                ".hero .runner .verdict", ".hero .runner .caption", ".hero .toplinks"):
        assert _z(css, sel) > vig, sel
