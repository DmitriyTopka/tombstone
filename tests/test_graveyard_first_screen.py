"""Task 21.2: the first screen of the graveyard page (R2.12)."""

from __future__ import annotations

import json
import re

from tombstone import cli
from tombstone.tombstones import _g_stop_reason, render_graveyard

from .test_graveyard import _record

TAGLINE = "Tombstone stops a Kiro agent when a task goes over its budget."
LEGEND = "Height is the price. A crack means the breaker stopped it."
HINT = "Click a cracked stone to read its receipt."


def _stopped(rid: str, name: str = "secret prompt words", **over) -> dict:
    base = dict(id=rid, task_id="unknown", name=name, outcome="killed", date="2026-11-06")
    base.update(over)
    return _record(**base)


def _visible(page: str) -> str:
    """Page text a reader sees: no title, style, script or tag attributes."""
    # The "Run the agent" block (R15) quotes the hook's refusal text, which says "turn".
    # The hero (R16) holds that block; the heading "The graveyard of this build" is R16.4.
    page = re.sub(r"<title>.*?</title>|<style>.*?</style>|<script>.*?</script>"
                  r'|<header class="hero[^"]*".*?</header>|<h2 id="pasth"[^>]*>.*?</h2>',
                  "", page, flags=re.S)
    return re.sub(r"<[^>]+>", " ", page)


def _receipt(reason: str, refusal: str = "TOMBSTONE: this turn over budget") -> dict:
    return {"reason": reason, "refusal": refusal, "estimate": "", "kill_ran": False, "calls": []}


def test_tagline_legend_and_headline_order():
    page = render_graveyard([_record()])
    assert TAGLINE in page and LEGEND in page
    assert page.index(TAGLINE) < page.index("<h1>")


def test_exactly_three_figures():
    page = render_graveyard([_record(), _stopped("s/1")])
    block = re.search(r'<dl class="proof">(.*?)</dl>', page, re.S).group(1)
    assert re.findall(r"<dt>(.*?)</dt>", block) == [
        "tasks buried", "stopped by the breaker", "the most expensive task"]


def test_hint_only_with_a_stopped_stone():
    assert HINT not in render_graveyard([_record()])
    assert HINT not in render_graveyard([])
    assert HINT in render_graveyard([_record(), _stopped("s/1")])


def test_links_only_when_given_and_escaped():
    plain = render_graveyard([_record()])
    assert "<a " not in plain
    only_repo = render_graveyard([_record()], repo="https://example.com/r")
    assert 'href="https://example.com/r"' in only_repo and "Demo video" not in only_repo
    page = render_graveyard([_record()], video='https://v.test/?a=1&b="<x>"', repo="https://r.test")
    assert 'href="https://v.test/?a=1&amp;b=&quot;&lt;x&gt;&quot;"' in page
    assert '<x>' not in page.split("<main>")[0]
    assert "Demo video" in page and "Repository" in page


def test_cli_video_and_repo_options(tmp_path, monkeypatch):
    (tmp_path / ".kiro").mkdir()
    (tmp_path / "tombstones.json").write_text(
        json.dumps({"version": 1, "tombstones": [_record()]}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["graveyard", "--video", "https://v.test/a", "--repo", "https://r.test/b"]) == 0
    page = (tmp_path / ".tombstone" / "graveyard.html").read_text(encoding="utf-8")
    assert 'href="https://v.test/a"' in page and 'href="https://r.test/b"' in page
    assert cli.main(["graveyard"]) == 0
    assert "<a " not in (tmp_path / ".tombstone" / "graveyard.html").read_text(encoding="utf-8")


def test_unknown_task_stop_reads_live_stop_with_reason():
    recs = [
        _stopped("s/new", "late prompt", date="2026-11-08"),
        _stopped("s/old", "early prompt", date="2026-11-06"),
        _stopped("s/mid", "middle prompt", turns=[{"start": "2026-11-07T10:00:00Z"}], date="2026-11-07"),
    ]
    receipts = {"s/old": _receipt("over_cap"), "s/mid": _receipt("stall")}
    page = render_graveyard(recs, None, None, receipts)
    stones = re.findall(
        r'data-title="([^"]*)".*?<span class="tid">(.*?)</span>.*?<span class="epi">(.*?)</span>', page)
    assert stones == [
        ("Live stop 2, stalled", "Live stop 2", "stalled"),
        ("Live stop 1, over its cap", "Live stop 1", "over its cap"),
        ("Live stop 3, stopped", "Live stop 3", "stopped"),
    ]
    assert page == render_graveyard(recs, None, None, receipts)
    text = _visible(page.split('<section class="register"')[0])
    assert "no task" not in text.lower()
    assert "prompt" not in text


def test_stop_reason_words():
    assert _g_stop_reason(None) == "stopped"
    assert _g_stop_reason(_receipt("over_cap")) == "over its cap"
    assert _g_stop_reason(_receipt("stall")) == "stalled"
    assert _g_stop_reason(_receipt("manual")) == "stopped"
    assert _g_stop_reason(_receipt("over_cap", "TOMBSTONE: Task 3 blocked. Cap is 2.")) \
        == "blocked before it started"


def test_known_task_stop_keeps_its_task_name():
    page = render_graveyard([_stopped("s/1", "Parse sessions", task_id="2.1")])
    assert '<span class="tid">task 2.1</span>' in page
    assert '<span class="epi">Parse sessions</span>' in page


def test_no_visible_grave_or_turn():
    recs = [_record(), _stopped("s/1", "hello"), _record(id="u/1", task_id="unknown", name="orphan")]
    text = _visible(render_graveyard(recs)).lower()
    assert "grave" not in text and "turn" not in text
    empty = _visible(render_graveyard([])).lower()
    assert "grave" not in empty and "turn" not in empty
    assert "<title>The Graveyard" in render_graveyard(recs)
