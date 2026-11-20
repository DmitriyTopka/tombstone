"""Task 18: the stop receipt in the card of a stopped headstone (R2.11)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from tombstone import autopsy, cli
from tombstone.tombstones import render_graveyard

from .test_graveyard import _record

FIXTURES = Path(__file__).parent / "fixtures" / "autopsies"
TURN = "72aa495c-3780-47b2-9024-6d49567eb59a"
NO_RECEIPT = "No receipt was kept for this stop."


def _fixture_file() -> Path:
    return next(FIXTURES.glob("*.md"))


def _killed(**over) -> dict:
    base = dict(id=f"unknown/{TURN}", task_id="unknown", name="Burned turn",
                outcome="killed", date="2026-11-06", turns=[{"turn_id": TURN}])
    base.update(over)
    return _record(**base)


def _project(tmp_path, monkeypatch, *records):
    import json
    (tmp_path / ".kiro").mkdir()
    (tmp_path / "tombstones.json").write_text(
        json.dumps({"version": 1, "tombstones": list(records)}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path / ".tombstone" / "graveyard.html"


def test_parser_on_real_autopsy():
    path = _fixture_file()
    got = autopsy.parse_autopsy(path.read_text(encoding="utf-8"), path.name)
    assert got["reason"] == "over_cap"
    assert got["refusal"] == "TOMBSTONE: this turn over budget (est. 0.66 of 0.60). Stop."
    assert got["estimate"] == "0.662 credits"
    assert got["kill_ran"] is False and got["step_two"].startswith("no")
    assert got["date"] == "2026-11-06" and got["task_id"] == "unknown"
    assert len(got["calls"]) == 10
    assert got["calls"][0] == ("read_file", "Read File")
    assert got["calls"][1] == ("fs_write", "Write File")


def test_parser_rejects_malformed():
    assert autopsy.parse_autopsy("hello") is None
    assert autopsy.parse_autopsy("# Autopsy: x\n\n- Task id: 1\n") is None
    assert autopsy.parse_autopsy(None) is None


def test_match_by_turn_id_and_by_task_and_date(tmp_path):
    shutil.copy(_fixture_file(), tmp_path / _fixture_file().name)
    found = autopsy.read_autopsies([tmp_path])
    assert len(found) == 1
    assert autopsy.match_autopsy(_killed(), found) is found[0]
    # Turn id only in the file name or content, other ids do not match.
    assert autopsy.match_autopsy(_killed(id="unknown/zzz", turns=[{"turn_id": "zzz"}]), found) is None

    text = _fixture_file().read_text(encoding="utf-8").replace("Task id: unknown", "Task id: 3.2")
    (tmp_path / "2026-11-06_3-2.md").write_text(text, encoding="utf-8")
    found = autopsy.read_autopsies([tmp_path])
    rec = _killed(id="spec/3.2", task_id="3.2", turns=[])
    assert autopsy.match_autopsy(rec, found)["name"] == "2026-11-06_3-2.md"
    assert autopsy.match_autopsy(dict(rec, date="2026-11-07"), found) is None


def test_card_carries_receipt_escaped(tmp_path):
    text = _fixture_file().read_text(encoding="utf-8").replace(
        "TOMBSTONE: this turn over budget", 'TOMBSTONE: <b>"bad"</b> & over budget')
    (tmp_path / _fixture_file().name).write_text(text, encoding="utf-8")
    found = autopsy.read_autopsies([tmp_path])
    rec = _killed()
    page = render_graveyard([rec], None, None, {rec["id"]: autopsy.match_autopsy(rec, found)})
    assert 'data-receipt="yes"' in page
    assert "<b>" not in page.split("<script>")[0].split("<dialog")[0]
    assert "&lt;b&gt;&quot;bad&quot;&lt;/b&gt; &amp; over budget" in page
    assert 'data-rc-reason="over_cap"' in page
    assert 'data-rc-kill="did not run"' in page
    assert 'data-rc-estimate="0.662 credits est."' in page
    assert page.count("fs_write: Write File") == 1
    assert 'id="cr"' in page and "Receipt" in page


def test_stopped_without_autopsy_and_finished_have_no_receipt_data():
    page = render_graveyard([_killed(), _record()])
    assert page.count('data-receipt="no"') == 1
    assert 'data-receipt="yes"' not in page
    assert NO_RECEIPT in page
    done = render_graveyard([_record()])
    assert "data-receipt" not in re.sub(r"<script>.*?</script>", "", done, flags=re.S)


def test_no_dashes_or_external_requests():
    page = render_graveyard([_killed()])
    assert "\u2014" not in page and "\u2013" not in page
    assert "https://" not in page


def test_cli_reads_project_and_extra_dirs(tmp_path, monkeypatch):
    page = _project(tmp_path, monkeypatch, _killed())
    extra = tmp_path / "extra"
    extra.mkdir()
    shutil.copy(_fixture_file(), extra / _fixture_file().name)
    (extra / "2026-11-06_bad.md").write_text("# Autopsy: broken\n- STOP reason", encoding="utf-8")
    assert cli.main(["graveyard", "--autopsies", str(extra)]) == 0
    assert 'data-receipt="yes"' in page.read_text(encoding="utf-8")

    own = tmp_path / ".tombstone" / "autopsy"
    own.mkdir(parents=True)
    shutil.copy(_fixture_file(), own / _fixture_file().name)
    assert cli.main(["graveyard"]) == 0
    assert 'data-receipt="yes"' in page.read_text(encoding="utf-8")


def test_missing_autopsies_dir_exits_zero(tmp_path, monkeypatch, capsys):
    page = _project(tmp_path, monkeypatch, _killed())
    assert cli.main(["graveyard", "--autopsies", str(tmp_path / "nope")]) == 0
    err = capsys.readouterr().err
    assert err.count("autopsy directory skipped") == 1
    assert page.exists()
    assert 'data-receipt="no"' in page.read_text(encoding="utf-8")


def test_symlinked_autopsy_file_is_skipped(tmp_path):
    real = tmp_path / "real.md"
    shutil.copy(_fixture_file(), real)
    d = tmp_path / "d"
    d.mkdir()
    (d / "2026-11-06_link.md").symlink_to(real)
    assert autopsy.read_autopsies([d]) == []


def _inline_script(page: str) -> str:
    return re.search(r"<script>\n(.*?)\n</script>", page, re.S).group(1)


def _raw_break_in_string(script: str) -> bool:
    """True when a line break sits inside a JS string literal."""
    quote = None
    i = 0
    while i < len(script):
        ch = script[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
            elif ch == "\n":
                return True
        elif ch in "\"'":
            quote = ch
        i += 1
    return False


def test_page_script_has_no_raw_line_break_in_string():
    script = _inline_script(render_graveyard([_record()]))
    assert 'split("\\n")' in script
    assert not _raw_break_in_string(script)


def test_page_script_parses_with_node():
    import subprocess
    node = shutil.which("node")
    if node is None:
        import pytest
        pytest.skip("node not on PATH")
    script = _inline_script(render_graveyard([_record()]))
    js = "new Function(require('fs').readFileSync(0,'utf8'))"
    done = subprocess.run([node, "-e", js], input=script.encode(),
                          capture_output=True)
    assert done.returncode == 0, done.stderr.decode()
