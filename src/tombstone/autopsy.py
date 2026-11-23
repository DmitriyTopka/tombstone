"""Write the Markdown receipt after a stop (R7.1 to R7.4).

The receipt lives in ``.tombstone/autopsy/<date>_<task>.md``. The home
directory is replaced with ``~`` over the whole document before it is
written. A killed turn has no ``usage_summary`` in the session, so Kiro never
writes an actual cost for it: the receipt says so and shows the plan balance
change from the usage cache instead, never zero.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .safepath import guard, write_atomic
from .sidecar import SIDECAR

TOOL_CALLS = 10
CELL_LIMIT = 200
ACTUAL_HEADING = "## Actual cost (actual)"
BALANCE_PREFIX = "- Plan balance at stop (est.): "
NOT_WRITTEN = "not written by Kiro for a killed turn"


def scrub(text: str, home: Optional[Path] = None) -> str:
    """Replace the home directory and its resolved form with ``~`` (R7.4)."""
    base = Path(home) if home is not None else Path.home()
    forms = {str(base)}
    try:
        forms.add(str(base.resolve()))
    except OSError:
        pass
    for form in sorted(forms, key=len, reverse=True):
        if not form or form in ("/", "~"):
            continue
        text = re.sub(re.escape(form) + r"(?![\w.\-])", "~", text)
    return text


def _parse_time(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _read_usage(project: Path) -> tuple[Optional[float], Optional[datetime]]:
    """The cached plan balance and its fetch time. Reads only."""
    try:
        data = json.loads((project / SIDECAR / "usage.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    value = data.get("balance")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, None
    return float(value), _parse_time(data.get("fetched_at"))


def _cell(value: object) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    if len(text) > CELL_LIMIT:
        text = text[:CELL_LIMIT - 3] + "..."
    return text.replace("|", "\\|")


def _tool_calls(session) -> Optional[list[tuple]]:
    """The last tool calls of a V3 session, oldest first. None when unreadable."""
    from . import sessions

    path = getattr(session, "path", None)
    if getattr(session, "engine", None) != "v3" or path is None:
        return None
    root = sessions._v3_root(Path(path))
    if root is None:
        return None
    rows: list[tuple] = []
    for event in sessions._read_jsonl(root / "messages.jsonl"):
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "tool_call":
            continue
        args = payload.get("args")
        shown = payload.get("title") or args
        rows.append((payload.get("toolName"), shown, payload.get("status"),
                     event.get("timestamp")))
    return rows[-TOOL_CALLS:]


def _task_slug(stop: dict) -> str:
    task_id = stop.get("task_id")
    if task_id and task_id != "unknown":
        return re.sub(r"[./\\]", "-", str(task_id))
    return "unknown-" + re.sub(r"[^\w\-]", "-", str(stop.get("turn_id") or "turn"))


def _step_two(stop: dict) -> str:
    kill = stop.get("kill")
    warnings = stop.get("warnings") or []
    if isinstance(kill, dict):
        status = kill.get("status")
        if status == "killed":
            return "yes (processes ended)"
        if status == "no_process":
            return "skipped: no kiro-cli process bound to this project"
        return f"{status}"
    if warnings:
        return "skipped: " + "; ".join(str(w) for w in warnings)
    return "no (not yet; waits for the grace period and more requests after the stop)"


def _fmt(value: Optional[float]) -> str:
    return "unknown" if value is None else f"{value:.3f}"


def write_autopsy(project, stop: dict, session, state: dict,
                  home: Optional[Path] = None) -> Optional[Path]:
    """Write the receipt for ``stop`` and return its path (R7.1, R7.2).

    Returns None when the target is refused as a symlink or outside the
    project (nothing is written).
    """
    from . import stop as stop_module
    from . import tombstones

    project = Path(project)
    state = state if isinstance(state, dict) else {}
    when = _parse_time(stop.get("time")) or datetime.now(timezone.utc)
    task_id = stop.get("task_id")
    spec = stop.get("spec")
    name = None
    if task_id and task_id != "unknown":
        try:
            name = tombstones._name_for_task(project, spec, task_id)
        except Exception:  # noqa: BLE001 - the name is a nicety
            name = None
    task = state.get("current_task") if isinstance(state.get("current_task"), dict) else {}
    if not name:
        turn_state = state.get("turn") if isinstance(state.get("turn"), dict) else {}
        name = task.get("name") or turn_state.get("prompt_name")

    turn = None
    for candidate in getattr(session, "turns", []) or []:
        if candidate.turn_id == stop.get("turn_id"):
            turn = candidate
    if turn is None and getattr(session, "turns", None):
        turn = session.turns[-1]

    requests = stop.get("requests_at_stop")
    if requests is None and turn is not None:
        requests = turn.requests
    estimate = stop.get("estimate")
    balance, fetched = _read_usage(project)
    before = balance if (balance is not None and fetched is not None
                         and fetched <= when) else None

    # The refusal text is not stored in the session; the STOP file has it.
    stop_file = stop_module.read_stop(project) or {}
    refusal = stop_file.get("message") or stop.get("message") or ""

    quiet = stop.get("quiet_minutes")
    lines = [
        f"# Autopsy: task {task_id or 'unknown'}" + (f" - {name}" if name else ""),
        "",
        f"- Task id: {task_id or 'unknown'}",
        f"- Task name: {name or 'unknown'}",
        f"- Engine: {getattr(session, 'engine', None) or 'unknown'}",
        f"- Model: {getattr(session, 'model', None) or 'unknown'}",
        f"- Requests at stop: {requests if requests is not None else 'unknown'}",
        "- Cost per request: unknown (no actual cost yet)",
        f"- Estimate at stop (est.): {_fmt(estimate if isinstance(estimate, (int, float)) else None)} credits",
        "- Actual after the turn: pending",
        f"- Quiet project: {_fmt_minutes(quiet)} minutes",
        f"- STOP time: {when.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- STOP reason: {stop.get('reason') or 'unknown'}",
        f"- Refusal text: {refusal or 'unknown'}",
        f"- Step two (kill): {_step_two(stop)}",
        BALANCE_PREFIX + (f"{before:.3f} credits" if before is not None else "unknown"),
        "",
        f"## Last {TOOL_CALLS} tool calls",
        "",
    ]
    calls = _tool_calls(session)
    if calls is None:
        lines.append("Tool calls are not available for this session format.")
    elif not calls:
        lines.append("No tool calls found in the session.")
    else:
        lines += ["| Tool | Title or args | Status | Time |", "|---|---|---|---|"]
        for tool, shown, status, stamp in calls:
            lines.append(f"| {_cell(tool)} | {_cell(shown)} | {_cell(status)} | {_cell(stamp)} |")
    lines.append("")

    target = project / SIDECAR / "autopsy" / f"{when.strftime('%Y-%m-%d')}_{_task_slug(stop)}.md"
    if not guard(project, target):
        # Symlinked sidecar or receipt path: nothing is written.
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_atomic(target, scrub("\n".join(lines), home))
    except OSError:
        return None
    return target


def _fmt_minutes(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unknown"
    # One decimal under 10 minutes, whole minutes otherwise (same as the STOP).
    return f"{value:.1f}" if value < 10 else f"{value:.0f}"


_RECEIPT_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}_[^/\\\x00]+\.md$")


def safe_autopsy_path(project, raw: str) -> Optional[Path]:
    """Validate a receipt path taken from state.json. None when refused.

    The path is rebuilt from the trusted autopsy dir plus a file name that
    matches ``<date>_<task>.md`` with no separators. It must sit directly in
    that dir (so ``../x.md`` and absolute paths elsewhere are refused), and
    neither the file nor any component under the project may be a symlink.
    The resolved result must stay inside the resolved autopsy dir.
    """
    try:
        project = Path(project)
        base = project / SIDECAR / "autopsy"
        given = Path(raw)
        if not given.is_absolute():
            given = project / given
        given = Path(os.path.normpath(str(given)))
        if given.parent != Path(os.path.normpath(str(base))):
            return None
        if not _RECEIPT_NAME.match(given.name) or ".." in given.name.split("/"):
            return None
        target = base / given.name
        for part in (project / ".kiro", project / SIDECAR, base, target):
            if part.is_symlink():
                return None
        if not target.is_file():
            return None
        resolved = target.resolve()
        if resolved.suffix != ".md" or resolved.parent != base.resolve():
            return None
        return target
    except (OSError, ValueError, RuntimeError):
        return None


STEP_TWO_PREFIX = "- Step two (kill): "


def step_two_line(kill: dict) -> str:
    """The receipt line for a finished kill step (R6.3, R7.1)."""
    stamp = str(kill.get("time") or "unknown time")
    if kill.get("status") == "killed":
        done = kill.get("terminated") or []
        forced = kill.get("killed") or []
        return (f"{STEP_TWO_PREFIX}yes (ran at {stamp}; {len(done)} processes "
                f"terminated, {len(forced)} killed)")
    warning = kill.get("warning") or "no kiro-cli process bound to this project"
    return f"{STEP_TWO_PREFIX}skipped at {stamp}: {warning}"


def update_step_two(project, raw_path, kill: dict,
                    home: Optional[Path] = None) -> bool:
    """Rewrite only the step two line of a stop's receipt (R6.3, R7.1).

    ``raw_path`` is ``state.stop.autopsy``. The path goes through
    ``safe_autopsy_path``. A missing, unreadable or symlinked receipt is left
    alone. Returns True when the line was rewritten. Never raises.
    """
    try:
        if not isinstance(raw_path, str) or not raw_path or not isinstance(kill, dict):
            return False
        from . import watcher

        project = Path(project)
        path = safe_autopsy_path(project, watcher.from_tilde(raw_path, home))
        if path is None or not guard(project, path):
            return False
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")
        index = next((i for i, line in enumerate(lines)
                      if line.startswith(STEP_TWO_PREFIX)), None)
        if index is None:
            return False
        lines[index] = scrub(step_two_line(kill), home)
        write_atomic(path, "\n".join(lines))
        return True
    except (OSError, ValueError):
        return False


def settle_autopsy(project, *, kiro_home: Optional[Path] = None,
                   home: Optional[Path] = None) -> bool:
    """Append the actual cost to the waiting autopsy once (R7.3, R4.2).

    Guarded by ``state.autopsy_pending``: with nothing pending it costs one
    small JSON read. The pending marker is cleared only after the section was
    written, so the append happens once. Called by the watcher loop and by the
    Stop hook path. Returns True when something was appended. Never raises.
    """
    from . import sessions, tombstones, watcher

    project = Path(project)
    try:
        state, _valid = tombstones._read_json(project / SIDECAR / "state.json")
        pending = state.get("autopsy_pending") if isinstance(state, dict) else None
        if not isinstance(pending, dict):
            return False
        turn_id = pending.get("turn_id")
        raw_path = pending.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            return False
        session_id = pending.get("session_id")

        def clear(current: dict) -> None:
            waiting = current.get("autopsy_pending")
            if isinstance(waiting, dict) and waiting.get("turn_id") == turn_id:
                current["autopsy_pending"] = None

        # The path comes from state.json, which anything in the project can
        # edit. Never write to it unless it is a plain receipt in our dir.
        path = safe_autopsy_path(project, watcher.from_tilde(raw_path, home))
        if path is None:
            from . import stop as stop_module
            stop_module._log_error(
                project, "autopsy append refused: pending path is not a plain "
                         ".md receipt inside .tombstone/autopsy")
            watcher.update_state(project, clear, home)
            return False

        found = sessions.find_session(
            project, session_id if isinstance(session_id, str) else None,
            kiro_home=kiro_home, v3_project_hash=state.get("v3_project_hash"))
        if found is None:
            return False
        turn = None
        for candidate in sessions.parse_session(found).turns:
            if candidate.turn_id == turn_id:
                turn = candidate
        if turn is None or turn.state != "closed" or turn.cost is None:
            return False

        written = append_actual(path, turn, home)

        # A missing receipt file will never be written, so clear it as well.
        watcher.update_state(project, clear, home)
        return written
    except Exception as exc:  # noqa: BLE001 - never break the watcher or a hook
        try:
            from . import stop as stop_module
            stop_module._log_error(
                project, f"autopsy append failed: {type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
        return False


def append_actual(path, turn, home: Optional[Path] = None) -> bool:
    """Append the actual cost section once (R7.3). Returns True when written.

    ``turn`` is a ``sessions.Turn`` or None. A killed turn has no cost, so the
    section says it was not written by Kiro and shows the plan balance change
    when the cache holds a value from before the stop and a later one.
    """
    path = Path(path)
    project_root = path.parents[2] if len(path.parents) > 2 else None
    if project_root is None or not guard(project_root, path):
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if ACTUAL_HEADING in text:
        return False

    cost = getattr(turn, "cost", None) if turn is not None else None
    lines = ["", ACTUAL_HEADING, ""]
    if cost is not None:
        requests = getattr(turn, "requests", None)
        lines.append(f"- Actual (actual): {cost:.3f} credits")
        if isinstance(requests, int) and requests > 0:
            lines.append(f"- Cost per request (actual): {cost / requests:.3f} credits")
    else:
        lines.append(f"- actual: {NOT_WRITTEN}")
        match = re.search(re.escape(BALANCE_PREFIX) + r"([0-9.]+) credits", text)
        before = float(match.group(1)) if match else None
        project = path.parents[2] if len(path.parents) > 2 else None
        after, fetched = _read_usage(project) if project is not None else (None, None)
        if before is None:
            lines.append("- Plan balance change (est.): unknown (no cached balance from before the stop)")
        elif after is None or fetched is None or after == before:
            lines.append(f"- Plan balance change (est.): unknown (balance before the stop was {before:.3f}; no newer reading yet)")
        else:
            lines.append(f"- Plan balance change (est.): {before - after:.3f} credits "
                         f"({before:.3f} before the stop, {after:.3f} later)")
    lines.append("")
    try:
        write_atomic(path, scrub(text.rstrip("\n") + "\n" + "\n".join(lines), home))
    except OSError:
        return False
    return True


# --- Reading receipts back for the graveyard card (R2.11) -------------------

MAX_AUTOPSY_BYTES = 256 * 1024
_FIELD = re.compile(r"^- ([^:]+): ?(.*)$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_autopsy(text: str, name: str = "") -> Optional[dict]:
    """Parse the Markdown that ``write_autopsy`` writes. None when malformed.

    Returns ``name`` (file name), ``task_id``, ``date`` (from the STOP time,
    else the file name), ``reason``, ``refusal``, ``estimate`` (text such as
    ``0.662 credits``), ``step_two`` (the line), ``kill_ran`` (bool) and
    ``calls`` (up to 10 ``(tool, title)`` pairs, oldest first). A file with no
    STOP reason line is not an autopsy.
    """
    if not isinstance(text, str) or not text.lstrip().startswith("# Autopsy"):
        return None
    fields: dict = {}
    calls: list = []
    in_table = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_table = line.startswith("## Last")
            continue
        if in_table and line.startswith("|"):
            cells = [c.strip().replace("\\|", "|") for c in _CELL_SPLIT.split(line.strip())[1:-1]]
            if len(cells) >= 2 and cells[0] != "Tool" and not set(cells[0]) <= set("-: "):
                calls.append((cells[0], cells[1]))
            continue
        match = _FIELD.match(line)
        if match and match.group(1) not in fields:
            fields[match.group(1)] = match.group(2).strip()
    reason = fields.get("STOP reason")
    if not reason:
        return None
    stamp = fields.get("STOP time", "")
    date = stamp[:10] if _DATE.fullmatch(stamp[:10]) else ""
    if not date:
        found = _DATE.match(name)
        date = found.group(0) if found else ""
    step = fields.get("Step two (kill)", "")
    estimate = fields.get("Estimate at stop (est.)", "")
    return {
        "name": name,
        "task_id": fields.get("Task id", "unknown"),
        "date": date,
        "reason": reason,
        "refusal": fields.get("Refusal text", ""),
        "estimate": "" if estimate == "unknown" else estimate,
        "step_two": step,
        "kill_ran": step.lower().startswith("yes"),
        "calls": calls[-TOOL_CALLS:],
    }


def _read_one(path: Path) -> Optional[dict]:
    from .safepath import open_nofollow

    try:
        with open_nofollow(path, "r", encoding="utf-8") as handle:
            text = handle.read(MAX_AUTOPSY_BYTES)
    except (OSError, ValueError):
        return None
    parsed = parse_autopsy(text, path.name)
    if parsed is not None:
        parsed["text"] = text
    return parsed


def read_autopsies(dirs) -> list:
    """Read every autopsy in ``dirs``. Never raises.

    A directory that is missing, unreadable or a symlink is skipped with one
    stderr line. A file that is a symlink, unreadable or malformed is skipped
    quietly. Order is stable: directory order, then file name.
    """
    found: list = []
    for raw in dirs or []:
        base = Path(raw).expanduser()
        try:
            if base.is_symlink() or not base.is_dir():
                raise OSError("not a plain directory")
            names = sorted(entry.name for entry in os.scandir(base)
                           if entry.name.endswith(".md") and entry.is_file(follow_symlinks=False))
        except OSError:
            sys.stderr.write(f"tombstone graveyard: autopsy directory skipped: {base}\n")
            continue
        for file_name in names:
            parsed = _read_one(base / file_name)
            if parsed is not None:
                found.append(parsed)
    return found


def match_autopsy(record: dict, autopsies: list) -> Optional[dict]:
    """The autopsy of a stopped record, or None (R2.11).

    First by turn id, when the file name or the file text holds one of the
    record's turn ids. Otherwise by task id and date, only for a known task.
    """
    if not isinstance(record, dict):
        return None
    ids = []
    for turn in record.get("turns") or []:
        if isinstance(turn, dict) and isinstance(turn.get("turn_id"), str) and turn["turn_id"]:
            ids.append(turn["turn_id"])
    rid = record.get("id")
    if isinstance(rid, str) and "/" in rid and rid.split("/", 1)[1]:
        ids.append(rid.split("/", 1)[1])
    ids = [i for i in ids if len(i) >= 16]  # a short id would match by chance
    for item in autopsies:
        if any(i in item["name"] or i in item.get("text", "") for i in ids):
            return item
    task_id = record.get("task_id")
    date = record.get("date")
    if isinstance(task_id, str) and task_id and task_id != "unknown" and date:
        for item in autopsies:
            if item["task_id"] == task_id and item["date"] == date:
                return item
    return None
