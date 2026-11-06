"""Tests for budget() and the stdio server tool list (task 9.2). No server is started."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone

from tombstone import cli, mcp_server

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def _write(tmp_path, records, toml=None, usage=None):
    (tmp_path / ".kiro" / "tombstone").mkdir(parents=True)
    specs = tmp_path / ".kiro" / "specs" / "demo"
    specs.mkdir(parents=True)
    (specs / "tasks.md").write_text("- [ ] 1. First task\n", encoding="utf-8")
    (tmp_path / "tombstones.json").write_text(
        json.dumps({"version": 1, "tombstones": records}), encoding="utf-8")
    if toml is not None:
        (tmp_path / "tombstone.toml").write_text(toml, encoding="utf-8")
    if usage is not None:
        (tmp_path / ".kiro" / "tombstone" / "usage.json").write_text(
            json.dumps(usage), encoding="utf-8")


def _rec(name, cost, spec="demo", basis="actual"):
    record = {"id": f"{spec}/{name}", "name": name, "spec": spec,
              "outcome": "finished", "task_type": "test"}
    record["actual" if basis == "actual" else "estimate"] = cost
    return record


def _no_probe(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("probe must not run")
    monkeypatch.setattr(subprocess, "run", boom)


def test_budget_with_similar_tasks_and_cached_balance(tmp_path, monkeypatch):
    _no_probe(monkeypatch)
    fetched = (NOW - timedelta(minutes=2)).isoformat()
    _write(tmp_path,
           [_rec("write parser tests", 10.0), _rec("write cap tests", 30.0)],
           toml="[caps]\nspec = 100\n",
           usage={"fetched_at": fetched, "balance": 55.5, "reason": None})
    result = mcp_server.budget(tmp_path, "write more tests", now=NOW)
    assert result["similar"]["median"] == 20.0
    assert result["similar"]["count"] == 2
    assert result["similar"]["basis"] == "actual"
    assert result["spec"]["cap"] == 100.0
    assert result["spec"]["spent"] == 40.0
    assert result["spec"]["remaining"] == 60.0
    assert result["plan_balance"]["value"] == 55.5
    assert result["plan_balance"]["age_minutes"] == 2.0
    assert result["plan_balance"]["basis"] == "actual"
    json.dumps(result)


def test_budget_without_similar_task_or_name(tmp_path, monkeypatch):
    _no_probe(monkeypatch)
    _write(tmp_path, [_rec("write parser tests", 10.0)])
    none_similar = mcp_server.budget(tmp_path, "rename module", now=NOW,
                                     refresh=False)
    assert none_similar["similar"]["median"] is None
    assert none_similar["similar"]["count"] == 0
    assert none_similar["similar"]["reason"] == "no similar task"
    assert none_similar["similar"]["task_cap"] == 40.0
    no_name = mcp_server.budget(tmp_path, None, now=NOW, refresh=False)
    assert no_name["similar"]["median"] is None
    assert no_name["similar"]["reason"] == "no task name given"
    assert no_name["spec"]["cap"] == 300.0


def test_budget_est_basis_when_estimates_used(tmp_path):
    _write(tmp_path, [_rec("write parser tests", 8.0, basis="est.")])
    result = mcp_server.budget(tmp_path, "write tests", now=NOW, refresh=False)
    assert result["similar"]["basis"] == "est."
    assert result["spec"]["basis"] == "est."


def test_budget_balance_missing_still_returns_other_numbers(tmp_path):
    _write(tmp_path, [_rec("write parser tests", 10.0)])
    result = mcp_server.budget(tmp_path, "write tests", now=NOW, refresh=False)
    assert result["plan_balance"]["value"] is None
    assert result["plan_balance"]["reason"]
    assert result["similar"]["median"] == 10.0
    assert result["spec"]["remaining"] == 290.0


def test_budget_probe_failure_is_reported_not_raised(tmp_path, monkeypatch):
    _write(tmp_path, [])

    def missing(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", missing)
    result = mcp_server.budget(tmp_path, None, now=NOW, refresh=True)
    assert result["plan_balance"]["value"] is None
    assert result["plan_balance"]["reason"] == "kiro-cli not found"
    assert result["spec"]["cap"] == 300.0


def test_budget_empty_project_without_files(tmp_path):
    result = mcp_server.budget(tmp_path, "x tests", now=NOW, refresh=False)
    assert result["spec"]["remaining"] == 300.0
    assert result["plan_balance"]["value"] is None
    assert result["spec"]["name"] is None


def test_tool_list_is_exactly_get_budget(tmp_path):
    reply = mcp_server.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, tmp_path)
    assert [t["name"] for t in reply["result"]["tools"]] == ["get_budget"]


def test_tool_call_in_process(tmp_path, monkeypatch):
    _no_probe(monkeypatch)
    _write(tmp_path, [_rec("write parser tests", 10.0)],
           usage={"fetched_at": datetime.now(timezone.utc).isoformat(),
                  "balance": 7.0, "reason": None})
    reply = mcp_server.handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "get_budget",
                    "arguments": {"task_name": "write tests"}}}, tmp_path)
    data = json.loads(reply["result"]["content"][0]["text"])
    assert data["plan_balance"]["value"] == 7.0
    assert data["similar"]["count"] == 1

def test_cli_mcp_subcommand_registered(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(mcp_server, "serve", lambda project=None: seen.update(p=project))
    assert cli.main(["mcp", "--project", str(tmp_path)]) == 0
    assert seen["p"] == tmp_path
