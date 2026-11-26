"""Task 35.2: the Devpost link and the closed stopped list (R24)."""

from __future__ import annotations

import json
import re

from tombstone import cli
from tombstone.tombstones import render_graveyard

from .test_graveyard import _record
from .test_graveyard_first_screen import _stopped


def test_devpost_link_only_with_the_option():
    assert "Devpost entry" not in render_graveyard([_record()])
    page = render_graveyard([_record()], devpost="https://d.test/p")
    assert page.count('<a href="https://d.test/p">Devpost entry</a>') == 3  # hero, header, footer
    assert page.index("Devpost entry") < page.index("<main>")
    assert page.rindex("Devpost entry") > page.index("<footer>")


def test_devpost_link_is_escaped_and_ordered_like_the_others():
    page = render_graveyard([_record()], video="https://v.test", repo="https://r.test",
                            devpost='https://d.test/?a=1&b="<x>"')
    assert 'href="https://d.test/?a=1&amp;b=&quot;&lt;x&gt;&quot;"' in page
    assert "<x>" not in page.split("<main>")[0]
    head = page.split("<main>")[0]
    assert head.index("Demo video") < head.index("Repository") < head.index("Devpost entry")
    assert "rel=" not in page.split("Devpost entry")[0].rsplit("<a ", 1)[1]


def test_cli_devpost_option(tmp_path, monkeypatch):
    (tmp_path / ".kiro").mkdir()
    (tmp_path / "tombstones.json").write_text(
        json.dumps({"version": 1, "tombstones": [_record()]}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["graveyard", "--devpost", "https://d.test/p"]) == 0
    page = (tmp_path / ".tombstone" / "graveyard.html").read_text(encoding="utf-8")
    assert 'href="https://d.test/p"' in page


def test_stopped_list_sits_in_a_closed_details_with_the_real_count():
    records = [_stopped(f"s/{i}") for i in range(3)]
    page = render_graveyard(records, included={r["id"]: "x.json" for r in records})
    m = re.search(r'<details class="stop-list wide"><summary>(.*?)</summary>(.*?)</details>',
                  page, re.S)
    assert m and m.group(1) == "Show the 3 live test runs"
    assert 'class="plot stopped wide"' in m.group(2) and 'class="stone' in m.group(2)
    assert "<details class=\"stop-list wide\" open" not in page
    assert "<h2>Stopped by the breaker</h2>" in page.split("<details")[0]
