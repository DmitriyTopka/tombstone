"""Write and check the STOP file and run the kill step.

Task 7.1 covers the STOP file, the pre-tool gate check and ``resume``. The
module imports only the standard library at the top so the hook path stays
fast (R11.5). Notification and kill arrive in later tasks.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .safepath import append_nofollow, guard, write_atomic
from .sidecar import SIDECAR

STOP_NAME = "STOP"

# Printed when the STOP file exists but cannot be read or has no message.
GENERIC_MESSAGE = "TOMBSTONE: stopped. Run tombstone resume."

REASONS = ("over_cap", "stall", "manual")


def stop_path(project: Path) -> Path:
    """Path of the STOP file for ``project``."""
    return Path(project) / SIDECAR / STOP_NAME


def _iso(value: Optional[datetime]) -> str:
    moment = value if value is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_stop(
    project: Path,
    reason: str,
    message: str,
    task_id: Optional[str] = None,
    estimate: Optional[float] = None,
    requests_at_stop: Optional[int] = None,
    time: Optional[datetime] = None,
    spec: Optional[str] = None,
    session_id: Optional[str] = None,
    live: Optional[float] = None,
) -> Path:
    """Write ``.tombstone/STOP`` atomically and return its path (R6.1).

    The file is JSON with the reason, the one-line message, the task id, the
    estimate (est.), the request count at the stop and the time. It lives
    inside the project sidecar directory only (R6.5).
    """
    target = stop_path(project)
    if not guard(project, target):
        # Symlinked sidecar or STOP path: nothing is written.
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "reason": reason,
        "message": message,
        "task_id": task_id,
        "spec": spec,
        "estimate": estimate,
        "requests_at_stop": requests_at_stop,
        "time": _iso(time),
        "session_id": session_id,
    }
    if live is not None:
        payload["live"] = live
    try:
        write_atomic(target, json.dumps(payload, indent=2) + "\n")
    except OSError:
        # A symlink planted at STOP after the guard: nothing is written.
        pass
    return target


def read_stop(project: Path) -> Optional[dict]:
    """Return the STOP content, ``{}`` when unreadable, None when absent."""
    target = stop_path(project)
    if not target.exists():
        return None
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def check_stop(project: Path) -> Optional[str]:
    """One-line stderr message while STOP exists, else None (R6.2, R6.6).

    A missing file is the fast path (one ``stat``). An unreadable or
    malformed file still blocks, with a generic message.
    """
    content = read_stop(project)
    if content is None:
        return None
    message = content.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip().splitlines()[0]
    return GENERIC_MESSAGE


NOTIFY_TITLE = "Tombstone"
NOTIFY_TIMEOUT = 5

_OSASCRIPT = (
    "on run argv",
    "display notification (item 1 of argv) with title (item 2 of argv)",
    "end run",
)


def _log_error(project: Optional[Path], text: str) -> None:
    """Append one line to ``errors.log``. Never raises."""
    if project is None:
        return
    try:
        sidecar = Path(project) / SIDECAR
        if not guard(project, sidecar / "errors.log"):
            return
        sidecar.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        append_nofollow(sidecar / "errors.log", f"{stamp} {text}\n")
    except OSError:
        pass


def notify(message: str, project: Optional[Path] = None,
           title: str = NOTIFY_TITLE) -> bool:
    """Show a local notification (R6.7). Returns True when it was sent.

    macOS uses ``osascript`` and Linux uses ``notify-send``. The text is passed
    as an argument, never spliced into a script. Any failure is logged to
    ``errors.log`` and ignored. No network call.
    """
    import subprocess
    import sys

    if sys.platform == "darwin":
        cmd = ["osascript"]
        for line in _OSASCRIPT:
            cmd += ["-e", line]
        cmd += [message, title]
    elif sys.platform.startswith("linux"):
        cmd = ["notify-send", "--", title, message]
    else:
        _log_error(project, f"notify skipped: unsupported platform {sys.platform}")
        return False
    try:
        result = subprocess.run(
            cmd, check=False, capture_output=True, timeout=NOTIFY_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log_error(project, f"notify failed: {type(exc).__name__}: {exc}")
        return False
    if result.returncode != 0:
        _log_error(project, f"notify failed: exit {result.returncode}")
        return False
    return True


def terminal_line(message: str, project: Optional[Path] = None, stream=None) -> bool:
    """Print the one-line stop message to the terminal (R6.7).

    Writes to ``stream`` (default stderr). Failures are logged and ignored.
    """
    import sys

    out = stream if stream is not None else sys.stderr
    try:
        out.write(message.strip().splitlines()[0] + "\n" if message.strip() else GENERIC_MESSAGE + "\n")
        out.flush()
    except (OSError, ValueError) as exc:
        _log_error(project, f"terminal line failed: {type(exc).__name__}: {exc}")
        return False
    return True


def announce(message: str, project: Optional[Path] = None) -> None:
    """Terminal line plus notification for a stop. Never raises."""
    try:
        terminal_line(message, project)
        notify(message, project)
    except Exception as exc:  # noqa: BLE001 - a stop must not crash on this
        _log_error(project, f"announce failed: {type(exc).__name__}: {exc}")


def resume(project: Path) -> bool:
    """Remove the STOP file and clear ``state.stop`` (R6.6).

    Returns True when a STOP file was removed. Only the STOP file and the
    ``stop`` entry of an existing ``state.json`` are touched.
    """
    target = stop_path(project)
    removed = False
    if guard(project, target):
        try:
            target.unlink()
            removed = True
        except FileNotFoundError:
            pass

    state_path = Path(project) / SIDECAR / "state.json"
    if state_path.is_file():
        from . import tombstones

        def clear(state: dict) -> dict:
            state.pop("stop", None)
            return state

        tombstones.update_json(state_path, clear, default={"version": 1})
    return removed


# --- step two: process lookup and kill (R6.3, R6.4, R6.5) ---------------------
#
# Measured tree of a V3 session (LESSONS.md, 2026-11-06): ``kiro-cli`` (root,
# cwd = project) -> Node agent -> tool commands, each in its own process group.
# So the kill walks the whole tree by PPID: leaves first, the root last.

KILL_TERM_WAIT = 5.0
NO_PROCESS_WARNING = ("no kiro-cli process is bound to this project directory; "
                      "step two skipped, nothing was terminated")


def _list_processes() -> list[tuple[int, int, str]]:
    """Return ``(pid, ppid, command)`` for every process. Never raises."""
    import subprocess
    import sys

    rows: list[tuple[int, int, str]] = []
    if sys.platform.startswith("linux"):
        try:
            names = os.listdir("/proc")
        except OSError:
            return rows
        for name in names:
            if not name.isdigit():
                continue
            try:
                with open(f"/proc/{name}/stat", "rb") as handle:
                    stat = handle.read().decode("utf-8", "replace")
                # The command name sits in parentheses and may hold spaces.
                ppid = int(stat[stat.rindex(")") + 2:].split()[1])
                with open(f"/proc/{name}/cmdline", "rb") as handle:
                    raw = handle.read().replace(b"\0", b" ").decode("utf-8", "replace")
            except (OSError, ValueError, IndexError):
                continue
            rows.append((int(name), ppid, raw.strip()))
        return rows
    try:
        result = subprocess.run(
            ["ps", "-Ao", "pid=,ppid=,command="],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return rows
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]),
                         parts[2] if len(parts) > 2 else ""))
        except ValueError:
            continue
    return rows


def _process_cwd(pid: int) -> Optional[str]:
    """Current directory of ``pid``, or None when it cannot be read."""
    import subprocess
    import sys

    if sys.platform.startswith("linux"):
        try:
            return os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return None
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def _same_dir(left: Optional[str], right: Path) -> bool:
    if not left:
        return False
    try:
        return os.path.realpath(left) == os.path.realpath(str(right))
    except OSError:
        return False


def _is_kiro_cli(command: str) -> bool:
    first = command.split(None, 1)[0] if command.strip() else ""
    return os.path.basename(first).startswith("kiro-cli")


def _protected_pids(rows: list[tuple[int, int, str]]) -> set[int]:
    """This process and every ancestor of it, plus pid 0 and 1."""
    parent = {pid: ppid for pid, ppid, _ in rows}
    protected = {0, 1}
    pid = os.getpid()
    while pid and pid not in protected:
        protected.add(pid)
        pid = parent.get(pid, 0)
    return protected


def find_project_processes(project: Path, *, list_processes=None,
                           process_cwd=None) -> list[int]:
    """Pids to terminate for ``project``, leaves first and the root last (R6.3).

    The root is a ``kiro-cli`` process whose current directory is exactly the
    project directory. All of its descendants by PPID follow (the Node agent
    and the tool commands). Deeper processes come first. The watcher's own pid
    and its ancestors are never returned. Returns ``[]`` when no root matches.
    """
    rows = (list_processes or _list_processes)()
    cwd_of = process_cwd or _process_cwd
    project = Path(project)
    protected = _protected_pids(rows)

    roots = [pid for pid, _ppid, command in rows
             if pid not in protected and _is_kiro_cli(command)
             and _same_dir(cwd_of(pid), project)]
    if not roots:
        return []

    children: dict[int, list[int]] = {}
    for pid, ppid, _command in rows:
        children.setdefault(ppid, []).append(pid)

    depth: dict[int, int] = {}
    for root in roots:
        queue = [(root, 0)]
        while queue:
            pid, level = queue.pop()
            if pid in depth or pid in protected:
                continue
            depth[pid] = level
            queue.extend((child, level + 1) for child in children.get(pid, []))
    # Deepest first; the root sits at depth 0 so it comes last.
    return sorted(depth, key=lambda pid: (-depth[pid], pid))


def _parse_time(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _send(kill_fn, pid: int, sig: int) -> bool:
    """Send ``sig`` to ``pid``. False when the process is already gone."""
    try:
        kill_fn(pid, sig)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    return True


def _alive(kill_fn, pid: int) -> bool:
    try:
        kill_fn(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def maybe_kill(project: Path, *, requests_now: Optional[int] = None,
               now: Optional[datetime] = None,
               grace_seconds: float = 30.0, term_wait: float = KILL_TERM_WAIT,
               find=None, kill_fn=None, sleep=None, clock=None) -> dict:
    """Step two of a stop: end the project's kiro-cli tree (R6.3, R6.4).

    Reads ``state.stop`` (``time``, ``requests_at_stop``). Acts only when the
    grace has passed and ``requests_now`` is above ``requests_at_stop``. Then
    sends SIGTERM leaves first and the root last, waits up to ``term_wait``
    seconds and sends SIGKILL to survivors. With no matching process it kills
    nothing, stores a warning in ``state.stop`` and returns it so the autopsy
    can show it (R6.4). Runs at most once per stop. Touches only
    ``state.json`` inside the sidecar (R6.5). Returns a dict with ``status``:
    ``no_stop``, ``done``, ``waiting_grace``, ``not_growing``, ``no_process``
    or ``killed``.
    """
    import signal
    import time as time_module

    from . import tombstones

    project = Path(project)
    state_path = project / SIDECAR / "state.json"
    state, _valid = tombstones._read_json(state_path)
    entry = (state or {}).get("stop")
    if not isinstance(entry, dict):
        return {"status": "no_stop"}
    if entry.get("kill"):
        return {"status": "done"}

    started = _parse_time(entry.get("time"))
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if started is None or (current - started).total_seconds() < grace_seconds:
        return {"status": "waiting_grace"}
    baseline = entry.get("requests_at_stop")
    if not isinstance(requests_now, int) or not isinstance(baseline, int) \
            or requests_now <= baseline:
        return {"status": "not_growing"}

    kill_fn = kill_fn or os.kill
    sleep = sleep or time_module.sleep
    clock = clock or time_module.monotonic
    pids = (find or find_project_processes)(project)
    pids = [pid for pid in pids if pid > 1 and pid != os.getpid()]

    result: dict = {"status": "killed", "terminated": [], "killed": [],
                    "warning": None}
    if not pids:
        result["status"] = "no_process"
        result["warning"] = NO_PROCESS_WARNING
    else:
        for pid in pids:
            if _send(kill_fn, pid, signal.SIGTERM):
                result["terminated"].append(pid)
        deadline = clock() + term_wait
        survivors = [pid for pid in pids if _alive(kill_fn, pid)]
        while survivors and clock() < deadline:
            sleep(0.2)
            survivors = [pid for pid in survivors if _alive(kill_fn, pid)]
        for pid in survivors:
            if _send(kill_fn, pid, signal.SIGKILL):
                result["killed"].append(pid)

    def store(state_value: dict) -> dict:
        stop_entry = state_value.setdefault("stop", {})
        stop_entry["kill"] = {
            "status": result["status"],
            "time": _iso(None),
            "terminated": result["terminated"],
            "killed": result["killed"],
        }
        if result["warning"]:
            warnings = stop_entry.setdefault("warnings", [])
            if result["warning"] not in warnings:
                warnings.append(result["warning"])
        return state_value

    tombstones.update_json(state_path, store, default={"version": 1})
    if result["status"] in ("killed", "no_process"):
        _record_kill_in_autopsy(project, state_path)
    return result


def _record_kill_in_autopsy(project: Path, state_path: Path) -> None:
    """Rewrite the step two line of the stop's receipt. Never raises."""
    try:
        from . import autopsy, tombstones

        state, _valid = tombstones._read_json(state_path)
        entry = (state or {}).get("stop")
        if isinstance(entry, dict) and isinstance(entry.get("kill"), dict):
            kill = dict(entry["kill"])
            if kill.get("status") == "no_process":
                kill["warning"] = NO_PROCESS_WARNING
            autopsy.update_step_two(project, entry.get("autopsy"), kill)
    except Exception:  # noqa: BLE001 - the kill result stays in state.json
        pass


# --- step one orchestration (R5.5, R6.1, R6.7) --------------------------------


def _event_time(event) -> datetime:
    moment = _parse_time(getattr(event, "at", None))
    return moment if moment is not None else datetime.now(timezone.utc)


def safe_session_path(raw: str, kiro_home=None) -> Optional[Path]:
    """Validate a session path taken from state.json. None when refused.

    The path must resolve inside ``<kiro_home>/sessions`` and be a ``sess_*``
    directory or a V2 ``.json`` / ``.jsonl`` file. Never raises.
    """
    from . import watcher

    try:
        home = Path(kiro_home) if kiro_home is not None else Path.home() / ".kiro"
        root = (home / "sessions").resolve()
        candidate = Path(watcher.from_tilde(raw)).resolve()
        candidate.relative_to(root)
        if candidate == root:
            return None
        if candidate.is_dir() and candidate.name.startswith("sess_"):
            return candidate
        if candidate.is_file() and candidate.suffix in (".json", ".jsonl"):
            return candidate
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def _stop_session(project: Path, event, state: dict, kiro_home):
    """Parse the session of the stop for the autopsy, or None."""
    from . import sessions

    stored = state.get("session") if isinstance(state.get("session"), dict) else {}
    path = None
    raw = stored.get("path")
    if isinstance(raw, str) and raw:
        path = safe_session_path(raw, kiro_home)
        if path is None:
            _log_error(project, "ignored state.session.path outside the Kiro "
                       "sessions directory; falling back to find_session")
    if path is None:
        path = sessions.find_session(
            project, getattr(event, "session_id", None), kiro_home=kiro_home,
            v3_project_hash=state.get("v3_project_hash"),
        )
    if path is None:
        return None
    return sessions.parse_session(path)


def _write_killed_record(project: Path, event, state: dict, session) -> dict:
    """Upsert the record of the stopped task with outcome ``killed``."""
    from . import caps, sessions, tombstones

    task = state.get("current_task") if isinstance(state.get("current_task"), dict) else {}
    spec = getattr(event, "spec", None) or task.get("spec")
    task_id = getattr(event, "task_id", None) or task.get("task_id")
    turn_state = state.get("turn") if isinstance(state.get("turn"), dict) else {}
    turn_id = getattr(event, "turn_id", None) or turn_state.get("watch_turn_id") \
        or "unknown"
    started = _parse_time(turn_state.get("started_at"))
    stored_session = state.get("session") if isinstance(state.get("session"), dict) else {}
    engine = (session.engine if session is not None else None) \
        or stored_session.get("engine") or "unknown"
    model = session.model if session is not None else None
    session_id = getattr(event, "session_id", None) or stored_session.get("id") \
        or (session.session_id if session is not None else "unknown")

    if task_id:
        name = tombstones._name_for_task(project, spec, task_id)
        ref = tombstones.TaskRef(spec=spec, task_id=task_id, name=name,
                                 source="stop")
    else:
        name = turn_state.get("prompt_name") or (started.isoformat() if started else None)
        ref = tombstones.TaskRef(spec=spec, task_id="unknown", name=name,
                                 source="stop", turn_id=turn_id)

    turn = sessions.Turn(
        engine=engine, session_id=session_id, turn_id=turn_id, model=model,
        requests=getattr(event, "requests", None), cost=None, start=started,
        end=None, state="open",
    )
    estimate = getattr(event, "estimate", None)
    path = project / "tombstones.json"
    store = tombstones.load_tombstones(path)
    record = tombstones.build_record(
        ref, turn, engine=engine, model=model, estimate=estimate,
        outcome="killed", task_type=caps.task_type(name or ""),
        existing=tombstones._existing_record(store, tombstones._record_id(ref)),
    )

    def write(store_value: dict) -> dict:
        return tombstones.upsert_record(store_value, record)

    tombstones.update_json(
        path, write, default=tombstones._empty_store(),
        pending_apply=tombstones.apply_pending_record,
    )
    return record


def begin_stop(event, *, project: Optional[Path] = None, kiro_home=None,
               announce_fn=None, autopsy_fn=None) -> Optional[dict]:
    """Step one of a stop for a watcher ``event`` (R5.5, R6.1, R6.7).

    In order: write the STOP file, announce (terminal line and notification),
    store the ``stop`` entry in ``state.json``, call the autopsy when it
    exists, and write the ``killed`` record. Each later step runs even when an
    earlier one failed; failures go to ``errors.log``. Step two is left to
    ``maybe_kill`` on later polls. A second event while STOP exists changes
    nothing. Returns the stored ``stop`` entry, or None when STOP was already
    there. Writes only inside ``.tombstone/`` and ``tombstones.json``.
    """
    from . import watcher

    project = Path(project if project is not None else event.project)
    if stop_path(project).exists():
        return None

    when = _event_time(event)
    requests = getattr(event, "requests", None)
    write_stop(
        project, reason=event.kind, message=event.message,
        task_id=getattr(event, "task_id", None),
        estimate=getattr(event, "estimate", None),
        requests_at_stop=requests, time=when,
        spec=getattr(event, "spec", None),
        session_id=getattr(event, "session_id", None),
        live=getattr(event, "live", None),
    )

    try:
        (announce_fn or announce)(event.message, project)
    except Exception as exc:  # noqa: BLE001 - a stop must go on
        _log_error(project, f"announce failed: {type(exc).__name__}: {exc}")

    entry = {
        "reason": event.kind,
        "message": event.message,
        "detail": getattr(event, "detail", None),
        "time": _iso(when),
        "requests_at_stop": requests,
        "estimate": getattr(event, "estimate", None),
        "estimate_label": "est.",
        "live": getattr(event, "live", None),
        "task_id": getattr(event, "task_id", None),
        "spec": getattr(event, "spec", None),
        "session_id": getattr(event, "session_id", None),
        "turn_id": getattr(event, "turn_id", None),
        "quiet_minutes": getattr(event, "quiet_minutes", None),
        "autopsy": None,
    }

    def store(state: dict) -> None:
        state["stop"] = dict(entry)

    state = watcher.update_state(project, store)

    session = None
    try:
        session = _stop_session(project, event, state, kiro_home)
    except Exception as exc:  # noqa: BLE001
        _log_error(project, f"stop session lookup failed: {type(exc).__name__}: {exc}")

    write_fn = autopsy_fn
    if write_fn is None:
        from . import autopsy
        write_fn = getattr(autopsy, "write_autopsy", None)
    if write_fn is None:
        _log_error(project, "autopsy skipped: autopsy.write_autopsy not available yet")
    elif session is None:
        _log_error(project, "autopsy skipped: no session found for the stop")
    else:
        try:
            written = write_fn(project, dict(entry), session, state)
            if written is None:
                raise OSError("autopsy write refused (symlink or outside project)")
            entry["autopsy"] = str(written)

            def mark(current: dict) -> None:
                current.setdefault("stop", {})["autopsy"] = watcher.to_tilde(str(written))
                current["autopsy_pending"] = {
                    "path": watcher.to_tilde(str(written)),
                    "turn_id": entry["turn_id"],
                    "session_id": entry["session_id"],
                }

            watcher.update_state(project, mark)
        except Exception as exc:  # noqa: BLE001
            _log_error(project, f"autopsy failed: {type(exc).__name__}: {exc}")

    try:
        _write_killed_record(project, event, state, session)
    except Exception as exc:  # noqa: BLE001
        _log_error(project, f"killed record failed: {type(exc).__name__}: {exc}")
    return entry
