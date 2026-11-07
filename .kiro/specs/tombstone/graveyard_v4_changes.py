"""Graveyard v4 mockup on top of the real v3 output (with --include live_stops.json).

Usage: python3 build_v4.py <base.html> <out.html>

Changes against v3, all of them to port into render_graveyard:
1. Every headstone gets a shape class. Neighbours in a row never share a shape:
   shape = (index in row + crc32(row key)) % 5, where the row key is the plot
   number, "unmarked" or "stopped". A cross needs height; below 170 px it
   falls back to the slab.
2. Every stopped headstone gets one of four cracks: crack = (index * 3 + 1) % 4.
3. No ghost over stopped stones.
4. Stopped row: wide gaps (about one stone) and at most 8 stones per line.
   More room between stones inside a plot too.
5. Copy: the lede covers stopped turns; the stopped note says "All 15" when
   every stopped stone came from the include file.
"""
import re
import sys
import zlib

base, out = sys.argv[1:3]
page = open(base, encoding="utf-8").read()

SHAPES = ["arch", "slab", "cross", "gothic", "shoulder"]


def shape_rows(html):
    def do_row(m, key):
        row = m.group(0)
        i = -1

        def stone(sm):
            nonlocal i
            i += 1
            h = int(re.search(r"--hh:(\d+)px", sm.group(0)).group(1))
            s = SHAPES[(i + zlib.crc32(key.encode())) % 5]
            if s == "cross" and h < 170:
                s = "slab"
            cls = f"stone shape-{s}"
            if key == "stopped":
                cls += f" crack-{(i * 3 + 1) % 4}"
            return sm.group(0).replace('class="stone"', f'class="{cls}"', 1)

        return re.sub(r'<button type="button" class="stone"[^>]*>', stone, row)

    def plot(m):
        block = m.group(0)
        if 'class="plot stopped' in block:
            key = "stopped"
        elif 'class="plot unmarked' in block:
            key = "unmarked"
        else:
            k = re.search(r'<span class="num">([^<]*)</span>', block)
            key = k.group(1) if k else "plot"
        return re.sub(r'<div class="row".*?</div>', lambda r: do_row(r, key), block, count=1, flags=re.S)

    return re.sub(r'<div class="plot[^"]*".*?(?=<div class="plot|<div class="section-note|</div></section>)', plot, html, flags=re.S)


page = shape_rows(page)
page = page.replace('<span class="ghost" aria-hidden="true"></span>', "")
page = page.replace(
    "Each stone is one spec task Kiro finished, as tall as its price.",
    "Each stone is a spec task Kiro finished or a turn the breaker stopped, as tall as its price.")
page = re.sub(r"(\d+) of them are live test runs", lambda m: f"All {m.group(1)} are live test runs", page)

crack = {
    0: "M30 0 L25 24 L35 40 L21 66 L28 94 L16 140|M25 24 L13 30 M35 40 L46 49 M28 94 L40 104",
    1: "M72 0 L66 18 L74 36 L60 58 L66 80 L52 104 L58 140|M66 18 L80 22 M60 58 L48 62 M52 104 L40 112",
    2: "M0 52 L18 56 L30 48 L46 60 L58 54 L74 66 L100 62|M30 48 L28 36 M58 54 L62 42 M74 66 L78 80",
    3: "M48 0 L52 20 L44 34 L56 52|M56 52 L70 74 L64 96 L80 140|M56 52 L38 76 L44 98 L26 140",
}


def crack_url(d):
    parts = d.split("|")
    paths = f"<path d='{parts[0]}' fill='none' stroke='%23140e10' stroke-width='2.4'/>"
    for p in parts[1:]:
        paths += f"<path d='{p}' fill='none' stroke='%23140e10' stroke-width='1.4'/>"
    return ("url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 140' "
            f"preserveAspectRatio='none'>{paths}</svg>\") center/100% 100% no-repeat")


crack_css = "\n".join(
    f".stopped .stone.crack-{k}{{background:{crack_url(d)},var(--grain),"
    "radial-gradient(ellipse 70% 36% at 70% 10%,rgba(255,255,255,.22),transparent 70%),"
    "linear-gradient(255deg,#9a9596,#6e6a6d 42%,#433f46);background-blend-mode:normal,overlay,normal,normal}"
    for k, d in crack.items())

# the plinth band below the stone stays inside every clip-path
BAND = "112% 100%,112% calc(100% + 14px),-12% calc(100% + 14px),-12% 100%"
extra = f"""
/* v4: more room, two lines of stopped stones */
.field{{max-width:1700px;gap:96px 72px}}
.row{{gap:44px}}
.stopped .row{{gap:84px 96px;max-width:1660px;padding-top:40px}}
.unmarked .row{{gap:46px 40px}}
/* v4: five headstone shapes; height still is the price */
.stone.shape-arch{{border-radius:50% 50% 5px 5px / 30% 30% 5px 5px}}
.stone.shape-slab{{border-radius:3px;clip-path:polygon(7% 0,93% 0,100% 5%,100% 100%,{BAND},0 100%,0 5%)}}
.stone.shape-gothic{{border-radius:0 0 4px 4px;padding-top:30px;
  clip-path:polygon(0 24%,14% 9%,32% 2%,50% 0,68% 2%,86% 9%,100% 24%,100% 100%,{BAND},0 100%)}}
.stone.shape-shoulder{{border-radius:3px 3px 4px 4px;padding-top:24px;
  clip-path:polygon(0 12%,12% 12%,16% 4%,30% 0,70% 0,84% 4%,88% 12%,100% 12%,100% 100%,{BAND},0 100%)}}
.stone.shape-cross{{width:calc(var(--h) * .72);border-radius:0;padding-top:calc(var(--h) * .47);
  clip-path:polygon(40% 0,60% 0,60% 12%,80% 12%,80% 27%,60% 27%,60% 44%,96% 44%,100% 47%,100% 100%,{BAND},0 100%,0 47%,4% 44%,40% 44%,40% 27%,20% 27%,20% 12%,40% 12%)}}
.stone.shape-cross .tid{{order:0}}
.stone.shape-cross .epi{{display:none}}
.unmarked .stone.shape-gothic,.unmarked .stone.shape-shoulder{{padding-top:16px}}
.unmarked .stone.shape-cross{{padding-top:calc(var(--h) * .47)}}
{crack_css}
@media (max-width:640px){{
  .row{{gap:28px}}
  .stopped .row{{gap:56px 40px}}
}}
"""
page = page.replace("</style>", extra + "</style>", 1)
assert "\u2014" not in page and "\u2013" not in page
open(out, "w", encoding="utf-8").write(page)
print("written", out, "shapes:", {s: page.count("shape-" + s) for s in SHAPES}, "ghosts:", page.count('class="ghost"'))
