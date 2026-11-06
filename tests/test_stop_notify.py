"""Tests for the notification and the terminal line (task 7.2). subprocess is mocked."""

from __future__ import annotations

import io
import subprocess
import sys
from types import SimpleNamespace

from tombstone import stop


class _Recorder:
    def __init__(self, returncode=0, exc=None):
        self.calls = []
        self.returncode = returncode
        self.exc = exc

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if self.exc:
            raise self.exc
        return SimpleNamespace(returncode=self.returncode)


def test_notify_macos_passes_text_as_argument(monkeypatch, tmp_path):
    rec = _Recorder()
    monkeypatch.setattr(subprocess, "run", rec)
    monkeypatch.setattr(sys, "platform", "darwin")
    text = 'task "11" over; do shell script "x"'
    assert stop.notify(text, tmp_path) is True
    cmd = rec.calls[0]
    assert cmd[0] == "osascript"
    assert cmd[-2] == text
    assert all(text not in part for part in cmd[:-2])


def test_notify_linux(monkeypatch, tmp_path):
    rec = _Recorder()
    monkeypatch.setattr(subprocess, "run", rec)
    monkeypatch.setattr(sys, "platform", "linux")
    assert stop.notify("hello", tmp_path) is True
    assert rec.calls[0][0] == "notify-send"
    assert rec.calls[0][-1] == "hello"


def test_notify_missing_binary_logged_and_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", _Recorder(exc=FileNotFoundError("nope")))
    monkeypatch.setattr(sys, "platform", "linux")
    assert stop.notify("hello", tmp_path) is False
    log = (tmp_path / ".kiro" / "tombstone" / "errors.log").read_text()
    assert "notify failed" in log


def test_notify_nonzero_exit_logged(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", _Recorder(returncode=1))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert stop.notify("hello", tmp_path) is False
    assert "exit 1" in (tmp_path / ".kiro" / "tombstone" / "errors.log").read_text()


def test_notify_unsupported_platform_runs_nothing(monkeypatch, tmp_path):
    rec = _Recorder()
    monkeypatch.setattr(subprocess, "run", rec)
    monkeypatch.setattr(sys, "platform", "win32")
    assert stop.notify("hello", tmp_path) is False
    assert rec.calls == []


def test_terminal_line_writes_first_line():
    out = io.StringIO()
    assert stop.terminal_line("TOMBSTONE: stop.\nsecond", stream=out) is True
    assert out.getvalue() == "TOMBSTONE: stop.\n"


def test_terminal_line_failure_logged(tmp_path):
    class Broken:
        def write(self, _):
            raise OSError("closed")

        def flush(self):
            pass

    assert stop.terminal_line("x", tmp_path, stream=Broken()) is False
    assert "terminal line failed" in (tmp_path / ".kiro" / "tombstone" / "errors.log").read_text()


def test_announce_never_raises(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(subprocess, "run", _Recorder(exc=OSError("boom")))
    monkeypatch.setattr(sys, "platform", "linux")
    stop.announce("TOMBSTONE: stop.", tmp_path)
    assert "TOMBSTONE: stop." in capsys.readouterr().err
