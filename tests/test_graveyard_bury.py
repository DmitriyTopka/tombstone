"""Tests for "Bury your own session" (task 36, R25). Synthetic fixtures, no network."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tombstone import cli, demo, sessions, stop, tombstones, watcher
from tombstone.tombstones import render_graveyard

FIXTURES = Path(__file__).parent / "fixtures" / "bury"
TWO = FIXTURES / "sess_synth-two"
NOSPLIT = FIXTURES / "sess_synth-nosplit"
NODE = shutil.which("node")


def _node_read(text: str) -> list:
    """Run the page's own reader (``buryRead``) in node on ``text``."""
    script = (tombstones._BURY_READER_JS
              + "\nconst t=require('fs').readFileSync(0,'utf8');"
              + "process.stdout.write(JSON.stringify(buryRead(t)));")
    done = subprocess.run([NODE, "-e", script], input=text, capture_output=True,
                          text=True, check=True)
    return json.loads(done.stdout)


def _same(a: list, b: list) -> None:
    assert len(a) == len(b)
    for x, y in zip(a, b):
        assert x["live"] == y["live"]
        assert (x["price"] is None) == (y["price"] is None)
        if x["price"] is not None:
            assert x["price"] == pytest.approx(y["price"], abs=1e-9)
        assert x["credits"] == pytest.approx(y["credits"], abs=1e-9)


def test_series_two_turns_baseline_and_broken_lines():
    turns = sessions.turn_series(TWO)
    assert len(turns) == 2
    first, second = turns
    # the creditSplit before turn_start is the baseline (0.15); a broken line and a bad split are skipped
    assert first["credits"] == pytest.approx([0.10, 0.25, 0.55])
    assert first["price"] == pytest.approx(0.55) and first["live"] is True
    # the second turn is measured from the last good creditSplit before its start (0.70)
    assert second["credits"] == pytest.approx([0.20, 0.80])
    assert second["price"] == pytest.approx(0.80)


def test_series_turn_without_credit_split_is_one_step_at_its_price():
    priced, unpriced = sessions.turn_series(NOSPLIT)
    assert priced == {"credits": [0.42], "price": 0.42, "live": False}
    assert unpriced == {"credits": [0.0], "price": None, "live": False}


def test_series_never_raises_on_other_paths(tmp_path):
    assert sessions.turn_series(tmp_path) == []
    assert sessions.turn_series(tmp_path / "missing") == []


def test_first_over_is_the_live_figure_rule():
    credits = [0.10, 0.25, 0.55]
    assert sessions.first_over(credits, 0.25) == 3   # equal is not above
    assert sessions.first_over(credits, 0.2) == 2
    assert sessions.first_over(credits, 0.55) is None
    assert sessions.first_over([], 0.1) is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("session", [TWO, NOSPLIT, demo.packaged_session()])
def test_js_reader_matches_python(session):
    text = (session / "messages.jsonl").read_text(encoding="utf-8")
    _same(_node_read(text), sessions.turn_series(session))


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_js_reader_edge_cases():
    split = lambda m: json.dumps({"payload": {"type": "session_metadata", "key": "creditSplit",  # noqa: E731
                                              "value": {"main": m, "delegated": 0}}})
    start = lambda t: json.dumps({"payload": {"type": "turn_start", "turnId": t}})  # noqa: E731
    text = "\r\n".join([split(1.0), start("a"), split(3.0), start("a"), split(2.5), "not json", "[]"])
    out = _node_read(text)
    assert [t["credits"] for t in out] == [[2.0, 1.5]]  # a repeated turn_start is ignored
    assert _node_read("") == [] and _node_read("{}\n{") == []
    # the Python side agrees on the same text
    events = [json.loads(x) for x in text.split("\r\n") if x.startswith("{")]
    assert sessions.series_from_events(events)[0]["credits"] == [2.0, 1.5]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("session,cap", [(TWO, 0.2), (TWO, 0.55), (TWO, 0.0), (NOSPLIT, 0.3), (NOSPLIT, 0.5)])
def test_js_stop_step_and_refusal_match_python(session, cap):
    template = tombstones._bury_data([])["template"]
    script = (tombstones._BURY_READER_JS
              + "\nconst d=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              + "process.stdout.write(JSON.stringify(buryRead(d.text).map(t=>{"
              + "const at=buryFirstOver(t.credits,d.cap);"
              + "return {at:at,refusal:at?buryRefusal(d.template,t.credits[at-1],d.cap):null};})));")
    text = (session / "messages.jsonl").read_text(encoding="utf-8")
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True,
                          input=json.dumps({"text": text, "cap": cap, "template": template}))
    got = json.loads(done.stdout)
    for turn, item in zip(sessions.turn_series(session), got):
        assert item["at"] == sessions.first_over(turn["credits"], cap)
        if item["at"]:
            figure = turn["credits"][item["at"] - 1]
            message = watcher.OVER_CAP_TEMPLATE.format(
                label="this turn", basis="live", figure=watcher._fmt(figure), cap=watcher._fmt(cap))
            assert item["refusal"] == stop.refusal_text({"message": message})


def _bury_data(page: str) -> dict:
    match = re.search(r"var BURY_DATA=(\{.*?\});\n", page, flags=re.S)
    assert match
    return json.loads(match.group(1))


def test_embedded_samples_hold_only_numbers_and_labels():
    page = render_graveyard([])
    data = _bury_data(page)
    assert [s["label"] for s in data["samples"]] == ["The recorded demo session"]
    for sample in data["samples"]:
        assert set(sample) == {"label", "turns"}
        for turn in sample["turns"]:
            assert set(turn) == {"credits", "price", "live"}
            assert all(isinstance(v, float) for v in turn["credits"])
            assert turn["price"] is None or isinstance(turn["price"], float)
            assert isinstance(turn["live"], bool)
    assert "{figure}" in data["template"] and "{cap}" in data["template"]
    assert "sess_" not in json.dumps(data) and "/" not in json.dumps(data["samples"])


def test_cli_sample_options_embed_label_and_numbers(tmp_path, monkeypatch):
    (tmp_path / ".kiro").mkdir()
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "g.html"
    args = ["graveyard", "--out", str(out), "--sample", str(TWO), "--sample-label", "Two <turns> & more",
            "--sample", str(NOSPLIT)]
    assert cli.main(args) == 0
    page = out.read_text(encoding="utf-8")
    data = _bury_data(page)
    assert [s["label"] for s in data["samples"]] == ["Two <turns> & more", "Sample 2"]
    assert len(data["samples"][0]["turns"]) == 2
    assert "Two &lt;turns&gt; &amp; more" in page and "Two <turns>" not in page
    assert "synth-two" not in page and "synth-nosplit" not in page


def test_cli_sample_errors(tmp_path, monkeypatch, capsys):
    (tmp_path / ".kiro").mkdir()
    monkeypatch.chdir(tmp_path)
    assert cli.main(["graveyard", "--sample", str(tmp_path)]) == 1
    assert cli.main(["graveyard", "--sample-label", "x"]) == 1
    assert "tombstone graveyard:" in capsys.readouterr().err


def test_block_position_controls_and_texts():
    page = render_graveyard([])
    hero_end = page.index("</header>")
    bury = page.index('<section class="bury"')
    graveyard = page.index("The graveyard of this build")
    assert hero_end < bury < graveyard
    block = page[bury:page.index("</section>", bury)]
    assert "Bury your own session" in block and "Play it on the stone" in block
    assert 'type="file"' in block and 'id="bury-drop"' in block and 'id="bury-turn"' in block
    assert 'type="range"' in block
    assert "Your file is read in this browser and never sent anywhere. " \
           "Only credit figures are read; prompts and code are skipped." in block
    assert "~/.kiro/sessions/&lt;hash&gt;/sess_*/messages.jsonl" in block
    assert "the stop is computed in this browser with the watcher&#x27;s live-figure rule" in block
    assert "The recorded run above uses the full watcher" in block
    for label in ('for="bury-file"', 'for="bury-turn"', 'for="bury-cap"'):
        assert label in block


def test_without_javascript_the_block_says_so_and_hides_controls():
    page = render_graveyard([])
    assert "<noscript><p class=\"note\">This part needs JavaScript." in page
    assert '<div id="bury-ui" hidden>' in page


def test_page_makes_no_network_request_and_stays_small():
    page = render_graveyard([])
    for word in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "new Image", "EventSource"):
        assert word not in page
    assert not re.search(r"""(?:src|href)=["']https?:""", page)
    assert len(page.encode("utf-8")) < 1_500_000


def test_existing_player_hook_and_message_text():
    page = render_graveyard([])
    assert "window.TombstonePlay=function" in page
    assert "Tombstone would have written STOP at " in page
    assert "This turn went on to " in page
    assert "No Kiro CLI turn found in this file." in page
    assert "This Kiro version writes no live figure for this turn." in page
    assert "This file is larger than 20 MB" in page
    assert _bury_data(page)["max_bytes"] == 20 * 1024 * 1024


# --- task 36.4 (R25.3, R25.5): caption switch and playback duration ----------

def _node_eval(expr: str):
    script = tombstones._BURY_READER_JS + f"\nprocess.stdout.write(JSON.stringify({expr}));"
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_bury_caption_names_the_source_and_the_rule():
    for source in ("Sample: The recorded demo session", "Your file"):
        text = _node_eval(f"buryCaption({json.dumps(source)})")
        assert text.startswith(source)
        assert "computed in this browser" in text
        assert "live-figure rule" in text
        assert "recorded real Kiro CLI session" not in text
    assert _node_eval("buryCaption()").startswith("Your file")


def test_caption_switch_is_wired_to_play_and_to_the_original_controls():
    page = render_graveyard([])
    assert 'id="run-caption"' in page
    # the original caption is kept and restored by the same mode(false) path the slider and button use
    assert 'capOrig=capEl?capEl.textContent:""' in page
    assert "capEl.textContent=c&&typeof buryCaption===\"function\"?buryCaption(source):capOrig" in page
    assert 'slider.addEventListener("input",function(){mode(false);});' in page
    assert 'btn.addEventListener("click",function(){mode(false);});' in page
    assert "window.TombstonePlay=function(s){mode(true,s.source);" in page
    # the block passes "Sample: <label>" for a sample and "Your file" for a visitor's file
    assert 'source:src==="Your file"?"Your file":"Sample: "+src' in page
    assert html.escape(tombstones._RUN_CAPTION, quote=True) in page


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("steps", [1, 2, 3, 5, 10, 43, 100, 500, 5000])
def test_playback_lasts_between_4_and_12_seconds(steps):
    plan = _node_eval(f"buryPlan({steps},1500,1000)")
    assert 4000 <= plan["total"] <= 12000
    assert plan["gap"] >= 0 and plan["rise"] <= 900
    # total is heave + gaps between steps + the wait before the verdict
    assert plan["total"] == pytest.approx(1500 + (steps - 1) * plan["gap"] + plan["settle"])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_long_series_gets_a_shorter_rise_per_step():
    short = _node_eval("buryPlan(3,1500,1000)")
    long_ = _node_eval("buryPlan(43,1500,1000)")
    assert long_["gap"] < short["gap"] and long_["rise"] < short["rise"]


def test_run_uses_the_bounded_plan_only_for_bury_series():
    page = render_graveyard([])
    assert 'if(ovr&&typeof buryPlan==="function")' in page
    assert "SETTLE=pl.settle" in page and "SETTLE=1000;" in page
    assert 'stone.style.transitionDuration=pl.rise+"ms"' in page
    assert 'stone.style.transitionDuration=""' in page


def test_bury_block_sits_above_the_hero_edge():
    css = tombstones._BURY_CSS
    assert "isolation:isolate" in css and "z-index:9" in css
    assert "padding:72px 20px 44px" in css and "padding:72px 14px 36px" in css
