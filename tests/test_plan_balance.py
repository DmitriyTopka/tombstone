"""Tests for the /usage probe and cache (task 9.1). kiro-cli is always mocked."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tombstone import caps

SAMPLE = (Path(__file__).parent / "fixtures" / "usage_sample.txt").read_text(
    encoding="utf-8")
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def _cache(tmp_path):
    return tmp_path / ".tombstone" / "usage.json"


@pytest.fixture
def calls(monkeypatch):
    log = []

    def fake_run(cmd, **kwargs):
        log.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout=SAMPLE, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return log


def test_parse_sample_sums_plan_and_bonus():
    parsed, reason = caps.parse_usage(SAMPLE)
    assert reason is None
    assert parsed["plan_remaining"] == pytest.approx(48.85)
    assert parsed["bonus"] == [{"name": "kiroween-2026", "remaining": pytest.approx(3857.2)}]
    assert parsed["balance"] == pytest.approx(48.85 + 3857.2)


def test_parse_multiple_bonus_lines():
    text = ("a: 1/10 credits used, x\nb: 2.5/5 credits used\n"
            "Credits (0 of 50 covered in plan), 0%")
    parsed, _ = caps.parse_usage(text)
    assert parsed["balance"] == pytest.approx(50 + 9 + 2.5)
    assert len(parsed["bonus"]) == 2


def test_parse_plan_only_and_garbage():
    parsed, _ = caps.parse_usage("Credits (1.15 of 50 covered in plan), 2.3%")
    assert parsed["balance"] == pytest.approx(48.85)
    assert parsed["bonus"] == []
    assert caps.parse_usage("hello") == (None, "unparseable /usage output")


def test_probe_uses_outside_cwd_and_argument_list(tmp_path, calls):
    result = caps.plan_balance(tmp_path, NOW, refresh=True)
    cmd, kwargs = calls[0]
    assert cmd == ["kiro-cli", "chat", "--no-interactive", "/usage"]
    assert kwargs["timeout"] == 15
    assert not kwargs.get("shell")
    assert not Path(kwargs["cwd"]).resolve().is_relative_to(tmp_path.resolve())
    assert result.value == pytest.approx(3906.05)
    assert result.age_minutes == 0
    assert result.plan_remaining == pytest.approx(48.85)
    assert result.bonus == (("kiroween-2026", pytest.approx(3857.2)),)
    assert json.loads(_cache(tmp_path).read_text())["balance"] == pytest.approx(3906.05)


def test_cache_used_within_five_minutes(tmp_path, calls):
    caps.plan_balance(tmp_path, NOW, refresh=True)
    result = caps.plan_balance(tmp_path, NOW + timedelta(minutes=4), refresh=True)
    assert len(calls) == 1
    assert result.age_minutes == pytest.approx(4)
    assert result.value == pytest.approx(3906.05)


def test_refresh_after_five_minutes(tmp_path, calls):
    caps.plan_balance(tmp_path, NOW, refresh=True)
    caps.plan_balance(tmp_path, NOW + timedelta(minutes=5), refresh=True)
    assert len(calls) == 2


def test_hooks_read_cache_only(tmp_path, calls):
    result = caps.plan_balance(tmp_path, NOW, refresh=False)
    assert calls == []
    assert result.value is None and result.reason == "no usage cache"
    caps.plan_balance(tmp_path, NOW, refresh=True)
    stale = caps.plan_balance(tmp_path, NOW + timedelta(hours=1), refresh=False)
    assert len(calls) == 1
    assert stale.value == pytest.approx(3906.05)
    assert stale.age_minutes == pytest.approx(60)


@pytest.mark.parametrize("exc,reason", [
    (FileNotFoundError(), "kiro-cli not found"),
    (subprocess.TimeoutExpired("kiro-cli", 15), "/usage probe timed out"),
])
def test_failure_is_cached(tmp_path, monkeypatch, exc, reason):
    n = []

    def boom(cmd, **kwargs):
        n.append(1)
        raise exc

    monkeypatch.setattr(subprocess, "run", boom)
    first = caps.plan_balance(tmp_path, NOW, refresh=True)
    assert first.value is None and first.reason == reason
    again = caps.plan_balance(tmp_path, NOW + timedelta(minutes=2), refresh=True)
    assert len(n) == 1
    assert again.value is None and again.reason == reason
    assert caps.cached_balance(tmp_path) is None


def test_nonzero_exit_and_unparseable(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, stdout="", stderr=""))
    assert caps.plan_balance(tmp_path, NOW, True).reason == "/usage probe exited with 1"
    other = tmp_path / "b"
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, stdout="nothing", stderr=""))
    assert caps.plan_balance(other, NOW, True).reason == "unparseable /usage output"


def test_cached_balance_reads_probe_cache(tmp_path, calls):
    caps.plan_balance(tmp_path, NOW, refresh=True)
    assert caps.cached_balance(tmp_path) == pytest.approx(3906.05)
