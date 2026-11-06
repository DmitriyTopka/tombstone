"""Stdio MCP server exposing the get_budget tool.

``budget`` is a plain function so it can be tested without a server. It reads
``tombstones.json``, ``tombstone.toml`` and the cached ``/usage`` figure and
makes no model call (R8.5). ``serve`` is a minimal stdlib JSON-RPC 2.0 server
on stdio with exactly one tool, ``get_budget`` (R8.1).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def budget(project: Path, task_name: Optional[str] = None,
           now: Optional[datetime] = None, refresh: bool = True) -> dict:
    """The three budget numbers for the agent (R8.2, R8.4).

    Returns a dict with:
    - ``similar``: median cost of similar finished tasks with ``count`` and
      ``basis`` (``actual`` or ``est.``); ``median`` is None when there is no
      task name or no similar task, and ``task_cap`` is the fallback reference;
    - ``spec``: the active spec, ``cap``, ``spent``, ``remaining`` and ``basis``;
    - ``plan_balance``: ``value`` (actual), ``age_minutes`` and ``reason``.
      An unavailable balance is None with a reason and the rest still returns
      (R8.4).
    Every figure is in credits. Never raises on missing or corrupt files.
    """
    from . import caps, tombstones

    project = Path(project)
    now = now or datetime.now(timezone.utc)

    config = caps.load_config(project)
    state, valid = tombstones._read_json(
        project / tombstones.TOMBSTONE_DIR / "state.json")
    state = state if valid and state is not None else {}
    records = tombstones.load_tombstones(project / "tombstones.json").get(
        "tombstones") or []

    if task_name:
        sim = caps.median_similar(records, task_name)
        similar_reason = None if sim.value is not None else "no similar task"
        similar = {"median": sim.value, "count": sim.count, "basis": sim.basis,
                   "reason": similar_reason}
    else:
        similar = {"median": None, "count": 0, "basis": "est.",
                   "reason": "no task name given"}
    similar["task_cap"] = config.task_cap

    spec = tombstones.active_spec(project, state)
    spent, basis = caps.spec_spent(records, spec)
    spec_part = {
        "name": spec,
        "cap": config.spec_cap,
        "spent": round(spent, 4),
        "remaining": round(max(config.spec_cap - spent, 0.0), 4),
        "basis": basis,
    }

    try:
        balance = caps.plan_balance(project, now, refresh=refresh)
        plan = {
            "value": balance.value,
            "basis": "actual",
            "age_minutes": (round(balance.age_minutes, 1)
                            if balance.age_minutes is not None else None),
            "reason": balance.reason,
        }
    except Exception as error:  # noqa: BLE001 - the other numbers still return
        plan = {"value": None, "basis": "actual", "age_minutes": None,
                "reason": f"plan balance unavailable: {error.__class__.__name__}"}

    return {"similar": similar, "spec": spec_part, "plan_balance": plan}


SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
LATEST_PROTOCOL = SUPPORTED_PROTOCOLS[-1]

TOOL = {
    "name": "get_budget",
    "description": (
        "Budget before starting a task: median cost of similar tasks, spec "
        "cap and remaining budget, and plan balance with its age in minutes. "
        "All figures are credits."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "task_name": {
                "type": "string",
                "description": "Name of the task about to start (optional).",
            },
        },
    },
}


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


def handle_message(message, project: Path) -> Optional[dict]:
    """Handle one JSON-RPC message and return the reply, or None for none.

    Notifications (no ``id``) never get a reply.
    """
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid Request")
    method = message.get("method")
    is_request = "id" in message
    req_id = message.get("id")
    if not isinstance(method, str):
        return _error(req_id, -32600, "Invalid Request") if is_request else None
    params = message.get("params")
    if not isinstance(params, dict):
        params = {}

    if not is_request:
        return None  # notifications/initialized and any other notification

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
        from . import __version__

        result = {"protocolVersion": version, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "tombstone", "version": __version__}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        if params.get("name") != TOOL["name"]:
            return _error(req_id, -32602, f"Unknown tool: {params.get('name')}")
        args = params.get("arguments")
        args = args if isinstance(args, dict) else {}
        task_name = args.get("task_name")
        if task_name is not None and not isinstance(task_name, str):
            return _error(req_id, -32602, "task_name must be a string")
        import json

        text = json.dumps(budget(project, task_name))
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    else:
        return _error(req_id, -32601, f"Method not found: {method}")
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def serve(project: Optional[Path] = None) -> None:
    """Run the stdio server (newline-delimited JSON-RPC 2.0) until EOF.

    Standard library only on the startup path, so the first ``initialize``
    reply is not delayed by heavy imports.
    """
    import json
    import sys

    if project is None:
        from .cli import find_project_root

        project = find_project_root() or Path.cwd()
    project = Path(project)

    out = sys.stdout
    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode("utf-8"))
        except ValueError:
            reply = _error(None, -32700, "Parse error")
        else:
            try:
                reply = handle_message(message, project)
            except Exception as error:  # noqa: BLE001 - keep serving
                reply = _error(message.get("id") if isinstance(message, dict)
                               else None, -32603,
                               f"Internal error: {error.__class__.__name__}")
        if reply is not None:
            out.write(json.dumps(reply) + "\n")
            out.flush()
