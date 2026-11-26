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
    # R19.5: two background photos, stone, heap, hand, bones and 8 clods, each exactly once
    assert page.count("data:image/webp;base64,") == 14
    assert "--scene-d:url(" in page and "--scene-m:url(" in page
    assert "<img" not in page
    for url in re.findall(r"url\(([^)]{0,40})", page):
        assert url.lstrip("\"'").startswith(("data:", "%23", "#")), url
    for src in re.findall(r"""\\bsrc\\s*=\\s*["']([^"']+)""", page):
        assert src.startswith("data:"), src
    assert "https://" not in page and "http://" not in page.replace(
        "http://www.w3.org/2000/svg", "")


def test_page_stays_under_1_1_mb():
    assert len(_page().encode("utf-8")) < 1100 * 1024


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
    assert ".hero .earth:not(.on) .hstone,.hero .earth.heave .hstone{clip-path:inset(0 0 100% 0)}" in css
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

_IMAGES = ("headstone", "heap", "hand_reach", "bones") + tuple(f"clods/clod_{n}" for n in range(8))


def _raw_b64(name: str) -> str:
    import base64
    raw = resources.files("tombstone").joinpath("data", "scene", *f"{name}.webp".split("/")).read_bytes()
    return base64.b64encode(raw).decode("ascii")


def test_each_image_is_embedded_exactly_once():
    page = _page()
    for name in _IMAGES + ("scene_desktop", "scene_mobile"):
        assert page.count(_raw_b64(name)) == 1, name
    for var in ("img-stone", "img-heap", "img-hand", "img-bones", "clod0", "clod7"):
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


def test_hand_and_bones_are_hidden_at_rest_and_toned_to_the_moon():
    page = _page()
    css = _css(page).replace("\n", "")
    assert re.search(r"\.hero \.hand\{[^}]*overflow:hidden[^}]*opacity:0\}", css)
    assert re.search(r"\.hero \.bones\{[^}]*opacity:0", css)
    assert ".hero .earth.hup .hand{opacity:1}" in css
    assert ".hero.stopped .bones,.hero .earth.full .bones{opacity:1}" in css
    tone = re.search(r"\.hero \.ph\{[^}]*filter:([^;]*);", css).group(1)
    assert "brightness(.8)" in tone and "saturate(.55)" in tone and "hue-rotate(172deg)" in tone
    assert "linear-gradient(210deg" in css  # highlight from the upper right
    assert 'class="hand"' in page and 'class="ph bones"' in page
    # the stone's carved figure is centred in the sunken panel (x 20 to 80 percent)
    assert "left:29%;right:29%" in css


def test_hand_rises_from_the_second_step_and_sinks_at_the_split():
    js = _script(_page()).replace("\n", "")
    tick = js.split("function tick(){", 1)[1].split("if(earth){earth.classList.add", 1)[0]
    assert "if(i>=2){hand(i,steps);}" in tick and "finish(r)" in tick
    assert 'earth.classList.add("hup")' in js
    reset = js.split("function reset(){", 1)[1].split("function finish", 1)[0]
    assert '"hup","full"' in reset
    css = _css(_page()).replace("\n", "")
    sink = re.search(r"\.hero\.stopped \.hand \.hi\{[^}]*transition:transform \.8s", css)
    assert sink and "translateY(105%)" in sink.group(0) and "opacity:0" in sink.group(0)
    # bones come with the end of the run, together with the full heap
    finish = js.split("function finish(r){", 1)[1].split("function run()", 1)[0]
    assert 'earth.classList.add("full")' in finish


def test_reduced_motion_and_noscript_show_hand_sunk_and_bones_shown():
    page = _page()
    css = _css(page).replace("\n", "")
    assert '<header class="hero stopped"' in page
    reduce = css.split("@media (prefers-reduced-motion:reduce){  .hero .fogl", 1)[1].split("@media (max-width:900px)", 1)[0]
    assert ".hero .hand .hi" in reduce and ".hero .bones" in reduce and ".hero .heap" in reduce
    assert 'class="earth on" id="run-earth"' in page and " hup" not in page.split('id="run-earth"', 1)[0][-40:]


def test_page_is_one_file_with_no_external_requests_and_under_1_1_mb():
    page = _page()
    assert len(page.encode("utf-8")) < 1100 * 1024
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
    hand_w = _units(css, ".hero .hand", "width")
    hand_h = _units(css, ".hero .hand", "height")
    hand_left = _units(css, ".hero .hand", "left")
    bones_w = _units(css, ".hero .bones", "width")
    bones_h = _units(css, ".hero .bones", "height")
    bones_left = _units(css, ".hero .bones", "left")
    earth_w = _units(css, ".hero .earth", "width")
    stone_left = (earth_w - stone_w) / 2
    skull = 0.52  # the skull spans the top 52 percent of bones.webp
    for scale in (k["1280"], k["390"]):
        W, H = stone_w * scale, stone_h * scale
        assert 0.20 <= above * scale / H <= 0.25
        assert 1.6 <= heap_w * scale / W <= 1.8
        assert 0.25 <= hand_h * scale / H <= 0.30 and hand_w * scale / W <= 0.45
        assert hand_left + hand_w <= stone_left  # left of the stone, no overlap
        assert 0.65 <= bones_w * scale / W <= 0.80
        assert 0.15 <= skull * bones_h * scale / H <= 0.20
        assert bones_left >= stone_left + stone_w  # right of the stone, no overlap
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
    assert "color:#04060a" in carve and "rgba(205,220,245,.6)" in carve
    # the carve box (left 29 to 71 percent, top 49 percent) is centred on the panel (x 20 to 80, y 32 to 78)
    assert "left:29%;right:29%;top:49%" in carve
