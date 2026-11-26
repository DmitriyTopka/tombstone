"""Tests for the "Run the agent" block (task 26.3, R15). No Kiro, no network."""

from __future__ import annotations

import io
import json
import re
import time

from tombstone import cli, demo, stop
from tombstone.tombstones import render_graveyard


def _page() -> str:
    return render_graveyard([])


def _data(page: str) -> dict:
    match = re.search(r"var RUN_DATA=(\{.*?\});\n?\(function", page, flags=re.S)
    assert match
    return json.loads(match.group(1))


def test_trace_is_deterministic_and_fast():
    start = time.perf_counter()
    first = demo.trace()
    assert time.perf_counter() - start < 2
    assert first == demo.trace()
    assert len(first["credits"]) == 9
    assert len(first["caps"]) >= 20
    caps_seen = [row["cap"] for row in first["caps"]]
    under = [c for c in caps_seen if c < first["total"]]
    assert len(under) >= 20 and caps_seen[0] == 0.05
    assert caps_seen[-2] < first["total"] < caps_seen[-1]
    assert 0.30 in caps_seen and 0.75 in caps_seen
    assert first["default_cap"] == 0.75


def test_default_cap_matches_demo_run(tmp_path, capsys):
    assert demo.run(str(tmp_path / "scratch")) == 0
    out = capsys.readouterr().out
    row = next(r for r in demo.trace()["caps"] if r["cap"] == demo.DEMO_TASK_CAP)
    assert f"request {row['stop_at']:2d}: est. {row['figure']:.3f}" in out
    assert f"STOP: {row['refusal']}" in out
    assert f"refusal text: {row['refusal']}" in out


def test_every_cap_stops_where_the_total_crosses_it():
    trace = demo.trace()
    for row in trace["caps"]:
        expected = next((i + 1 for i, c in enumerate(trace["credits"]) if c > row["cap"]), None)
        assert row["stop_at"] == expected
        assert (row["refusal"] is None) == (expected is None)


def test_refusal_text_is_the_hooks_own():
    assert stop.refusal_text({"message": "TOMBSTONE: x\nsecond"}) == "TOMBSTONE: x"
    assert stop.refusal_text({}) == stop.GENERIC_MESSAGE


def test_trace_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    demo.trace()
    assert list(tmp_path.iterdir()) == []


def test_cap_above_total_has_no_stop():
    trace = demo.trace(caps_list=[1.45, 5.0])
    assert [r["stop_at"] for r in trace["caps"]] == [None, None]
    last = demo.trace()["caps"][-1]
    assert last["cap"] > demo.trace()["total"] and last["stop_at"] is None
    under = demo.trace()["caps"][-2]
    assert under["cap"] < demo.trace()["total"] and under["stop_at"] == 9
    assert demo.cap_grid(0.04) == []


def test_block_caption_labels_and_static_result():
    page = _page()
    for needle in (
            "Run the agent", ">Run</button>", 'id="run-cap"', 'aria-label="Cap in credits"',
            "A recorded real Kiro CLI session, scrubbed. For every cap on the slider, the "
            "stop point was computed by Tombstone&#x27;s own watcher code when this page "
            "was built.",
            ">STOP</p>", "hook exit code 2", "crack-1",
            "Without the breaker this session went on to 1.44 credits est."):
        assert needle in page, needle
    default = next(i for i, r in enumerate(_data(page)["caps"]) if r["cap"] == 0.75)
    assert f'value="{default}"' in page and "0.75 credits</output>" in page
    assert page.index('id="runner"') < page.index('id="ground"')
    assert page.index("</header>") < page.index('id="runner"')


def test_reduced_motion_and_phone_rules_exist():
    page = _page()
    assert "prefers-reduced-motion:reduce" in page
    assert "matchMedia" in page
    assert "max-width:640px" in page


def test_inline_json_cannot_close_the_script_tag():
    page = _page()
    assert page.count("<script>") == 1 and page.count("</script>") == 1
    body = page.split("<script>", 1)[1].split("</script>", 1)[0]
    assert "var RUN_DATA=" in body
    assert _data(page)["caps"]
    from tombstone.tombstones import _run_json
    nasty = _run_json({"x": "</script><!-- & \u2028"})
    assert "<" not in nasty and ">" not in nasty and "&" not in nasty
    assert json.loads(nasty)["x"] == "</script><!-- & \u2028"


def test_browser_only_looks_up_results():
    page = _page()
    script = page.split("<script>", 1)[1]
    assert "evaluate" not in script and "task_cap" not in script


def test_no_external_requests():
    page = _page()
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", page)
    assert "http://" not in page.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in page
    assert "@import" not in page and "fetch(" not in page and "XMLHttpRequest" not in page


def test_no_dash_characters_in_the_block():
    page = _page()
    assert "\u2014" not in page and "\u2013" not in page


def test_result_lines_are_in_the_blocks_flow():
    page = _page()
    css = page.split("<style>", 1)[1].split("</style>", 1)[0]
    rules: dict = {}
    for sel, body in re.findall(r"(\.runner[^{,]*)\{([^}]*)\}", css):
        rules.setdefault(sel, body)
    for sel in (".runner", ".runner .scene", ".runner .verdict"):
        body = rules[sel]
        assert "position:absolute" not in body and "position:fixed" not in body
        assert not re.search(r"(^|;)\s*(max-)?height:", body)
    assert "flow-root" in rules[".runner"]
    section = page.split('id="runner"', 1)[1].split("</section>", 1)[0]
    assert "position:absolute" not in section and "height:" not in section.replace("--rh", "")
    assert "STOP</p>" in section and "Without the breaker" in section
    assert section.index('id="run-verdict"') < section.index("Without the breaker")
    assert "0.75 credits" in section
