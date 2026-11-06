"""Subprocess tests for the stdlib stdio JSON-RPC server (R8.1)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tombstone import __version__


def _start(project: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "tombstone", "mcp", "--project", str(project)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8")


def _send(proc: subprocess.Popen, obj: dict) -> None:
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def _recv(proc: subprocess.Popen) -> dict:
    return json.loads(proc.stdout.readline())


def test_stdio_session(tmp_path):
    # A fresh usage cache keeps the call from probing kiro-cli.
    cache = tmp_path / ".kiro" / "tombstone"
    cache.mkdir(parents=True)
    (cache / "usage.json").write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "balance": 55.5, "reason": None}), encoding="utf-8")
    proc = _start(tmp_path)
    try:
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {"name": "t", "version": "0"}}})
        init = _recv(proc)
        assert init["id"] == 1
        assert init["result"]["protocolVersion"] == "2025-03-26"
        assert init["result"]["capabilities"] == {"tools": {}}
        assert init["result"]["serverInfo"] == {
            "name": "tombstone", "version": __version__}

        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "ping"})
        assert _recv(proc) == {"jsonrpc": "2.0", "id": 2, "result": {}}

        _send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        tools = _recv(proc)["result"]["tools"]
        assert [t["name"] for t in tools] == ["get_budget"]
        assert "task_name" in tools[0]["inputSchema"]["properties"]
        assert "required" not in tools[0]["inputSchema"]

        _send(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                     "params": {"name": "get_budget",
                                "arguments": {"task_name": "write tests"}}})
        call = _recv(proc)
        assert call["id"] == 4
        assert call["result"]["isError"] is False
        content = call["result"]["content"]
        assert len(content) == 1 and content[0]["type"] == "text"
        data = json.loads(content[0]["text"])
        assert set(data) == {"similar", "spec", "plan_balance"}
        assert data["plan_balance"]["value"] == 55.5

        _send(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                     "params": {"name": "nope", "arguments": {}}})
        assert _recv(proc)["error"]["code"] == -32602

        _send(proc, {"jsonrpc": "2.0", "id": 6, "method": "resources/list"})
        assert _recv(proc)["error"]["code"] == -32601
    finally:
        proc.stdin.close()
    assert proc.wait(timeout=10) == 0
    proc.stdout.close()
    proc.stderr.close()


def test_unknown_protocol_version_gets_latest(tmp_path):
    proc = _start(tmp_path)
    try:
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "1999-01-01"}})
        assert _recv(proc)["result"]["protocolVersion"] == "2025-11-25"
    finally:
        proc.stdin.close()
    assert proc.wait(timeout=10) == 0
    proc.stdout.close()
    proc.stderr.close()


def test_first_initialize_reply_under_300_ms(tmp_path):
    started = time.monotonic()
    proc = _start(tmp_path)
    try:
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-11-25"}})
        reply = _recv(proc)
        elapsed = time.monotonic() - started
        assert reply["id"] == 1
        assert elapsed < 0.3, f"initialize took {elapsed * 1000:.0f} ms"
    finally:
        proc.stdin.close()
    proc.wait(timeout=10)
    proc.stdout.close()
    proc.stderr.close()
