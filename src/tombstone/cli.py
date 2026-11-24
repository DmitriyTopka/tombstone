"""Command line entry point for the tombstone command.

``cli.main`` parses arguments with argparse and imports a command's module
only after parsing, so a hook path never pays for ``subprocess`` or
``tomllib`` it does not use (design: lazy imports per command).

Task 2 lands the entry point and ``tombstone cost``. Other subcommands are
registered as they are built; until then they report that they are not ready.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from .sidecar import LEGACY, SIDECAR, migrate as migrate_sidecar


def find_project_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from ``start`` to the first project root, or None.

    A project root is the first directory that contains a ``.kiro`` directory
    or a ``tombstone.toml`` file. Used so commands and hooks agree on where
    the project lives. Returns None when no marker is found up to the
    filesystem root.
    """
    here = Path(start) if start is not None else Path.cwd()
    try:
        here = here.resolve()
    except OSError:
        here = Path(start) if start is not None else Path.cwd()
    for directory in (here, *here.parents):
        if (directory / ".kiro").is_dir() or (directory / "tombstone.toml").is_file():
            return directory
    return None


# --- tombstone cost ---------------------------------------------------------


def _resolve_session(argument: str, project: Optional[Path],
                     kiro_home: Path):
    """Resolve the ``cost`` argument to a session path (R1.6).

    The argument may be:
    - a path to a V3 ``sess_*`` directory, its ``messages.jsonl``, or a V2
      ``<uuid>.json`` / ``.jsonl`` file;
    - a full session id or a unique id prefix, searched under ``kiro_home``.

    Returns ``(path, error)``: on success ``path`` is set and ``error`` is
    None; on failure ``path`` is None and ``error`` is a one-line reason.
    Never raises.
    """
    from . import sessions

    candidate = Path(argument).expanduser()
    if candidate.exists() and sessions.detect_format(candidate) is not None:
        return candidate, None

    # A path-like argument (has a separator) is only ever a path. If it did
    # not resolve above, do not treat it as an id.
    looks_like_path = "/" in argument or "\\" in argument
    if not looks_like_path:
        # Treat the argument as a session id or a unique id prefix.
        matches = _find_by_id(argument, kiro_home)
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            shown = ", ".join(sorted(p.name for p in matches)[:5])
            return None, f"ambiguous id prefix '{argument}' matches: {shown}"

        # Fall back to a full-id glob through find_session (cached hash path).
        if project is not None:
            found = sessions.find_session(project, argument, kiro_home=kiro_home)
            if found is not None:
                return found, None

    if candidate.exists():
        return None, f"unknown format: {argument}"
    return None, f"no session found for '{argument}'"


def _find_by_id(prefix: str, kiro_home: Path) -> list[Path]:
    """Session paths whose id starts with ``prefix`` under ``kiro_home``.

    Searches V3 ``sessions/*/sess_<prefix>*`` directories and V2
    ``sessions/cli/<prefix>*.json`` files. A leading ``sess_`` on the prefix
    is tolerated. Returns the matched session paths. Never raises.
    """
    stem = prefix[len("sess_"):] if prefix.startswith("sess_") else prefix
    found: list[Path] = []

    sessions_root = kiro_home / "sessions"
    try:
        v3_dirs = sorted(sessions_root.glob(f"*/sess_{stem}*"))
    except OSError:
        v3_dirs = []
    for directory in v3_dirs:
        if (directory / "session.json").is_file():
            found.append(directory)

    cli_root = sessions_root / "cli"
    try:
        v2_files = sorted(cli_root.glob(f"{stem}*.json"))
    except OSError:
        v2_files = []
    for json_file in v2_files:
        found.append(json_file)

    return found


def _fmt_credits(value: Optional[float], label: str) -> str:
    """Format a credit figure with at least three decimals and a label."""
    if value is None:
        return "n/a"
    return f"{value:.3f} credits ({label})"


def _fmt_requests(value: Optional[int], label: str) -> str:
    if value is None:
        return "n/a requests"
    return f"{value} requests ({label})"


def _fmt_time(value) -> str:
    return value.isoformat() if value is not None else "-"


def _turn_line(turn) -> str:
    """One output line for a turn (R1.3)."""
    if turn.state == "open":
        cost_label = "est."
        req_label = "est."
    else:
        cost_label = "actual"
        req_label = "actual"

    parts = [
        turn.engine,
        turn.model or "unknown model",
        _fmt_requests(turn.requests, req_label),
    ]
    if turn.state == "closed" and turn.cost is None:
        parts.append(f"unpriced: {turn.unpriced_reason or 'unknown reason'}")
    else:
        parts.append(_fmt_credits(turn.cost, cost_label))
    parts.append(f"start {_fmt_time(turn.start)}")
    parts.append(f"end {_fmt_time(turn.end)}")
    return "  ".join(parts)


def _cost_report(session) -> tuple[list[str], bool]:
    """Build the report lines for a session and whether any turn was read.

    Returns ``(lines, had_turn)``. ``had_turn`` is False when no turn could
    be read, which the caller turns into exit 1 (design: Error Handling).
    """
    lines: list[str] = []
    if session.engine is None:
        lines.append(f"unknown format: {session.path}")
        return lines, False
    if not session.turns:
        lines.append(f"{session.engine} session {session.session_id}: no turns")
        return lines, False

    lines.append(f"{session.engine} session {session.session_id}")
    total_actual = 0.0
    total_est = 0.0
    total_requests = 0
    saw_actual = False
    saw_est = False
    for turn in session.turns:
        lines.append("  " + _turn_line(turn))
        if turn.requests is not None:
            total_requests += turn.requests
        if turn.cost is not None:
            if turn.state == "open":
                total_est += turn.cost
                saw_est = True
            else:
                total_actual += turn.cost
                saw_actual = True

    if saw_actual and not saw_est:
        total = _fmt_credits(total_actual, "actual")
    elif saw_est and not saw_actual:
        total = _fmt_credits(total_est, "est.")
    elif saw_actual and saw_est:
        total = (f"{total_actual:.3f} credits (actual) + "
                 f"{total_est:.3f} credits (est.)")
    else:
        total = "0.000 credits (actual)"
    lines.append(f"total: {total}, {total_requests} requests")
    return lines, True


def cmd_cost(args: argparse.Namespace) -> int:
    """``tombstone cost <session>``: price one session (R1.3, R1.6)."""
    project = find_project_root()
    kiro_home = Path(args.kiro_home).expanduser() if args.kiro_home \
        else Path.home() / ".kiro"

    _reconcile_pending_quietly(project, kiro_home if args.kiro_home else None)

    path, error = _resolve_session(args.session, project, kiro_home)
    if error is not None:
        print(error, file=sys.stderr)
        return 1

    from . import sessions

    session = sessions.parse_session(path)
    lines, had_turn = _cost_report(session)
    stream = sys.stdout if had_turn else sys.stderr
    for line in lines:
        print(line, file=stream)
    return 0 if had_turn else 1


# --- tombstone hook ---------------------------------------------------------


def _read_stdin_json() -> dict:
    """Read and parse the hook STDIN JSON. Never raises; {} on any problem."""
    import json

    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# Set by ``cmd_hook`` from ``--project`` for the duration of one hook call.
_PROJECT_OVERRIDE: Optional[Path] = None


def _hook_project_root(stdin: dict) -> Optional[Path]:
    """Find the project root for a hook.

    ``--project`` wins for every event; otherwise STDIN ``cwd``, otherwise
    the process cwd.
    """
    if _PROJECT_OVERRIDE is not None:
        return _PROJECT_OVERRIDE
    cwd = stdin.get("cwd") if isinstance(stdin, dict) else None
    start = Path(cwd) if isinstance(cwd, str) and cwd else None
    return find_project_root(start)


def _hook_post_tool(stdin: dict) -> int:
    """``tombstone hook post-tool``: refresh the tasks snapshot (R2.3, R2.4).

    Does real work only when the tool input path matches
    ``.kiro/specs/<spec>/tasks.md``; any other tool call returns at once. The
    hook never fails the agent: on any error it logs and exits 0.
    """
    from . import tombstones

    spec = tombstones.stdin_tasks_spec(stdin)
    if spec is None:
        return 0

    project = _hook_project_root(stdin)
    if project is None:
        return 0

    turn_id = tombstones.stdin_turn_id(stdin)
    state_path = project / tombstones.TOMBSTONE_DIR / "state.json"
    tombstones.update_json(
        state_path,
        lambda state: tombstones.refresh_snapshot(
            state, project, spec, stdin=stdin, turn_id=turn_id
        ),
        default={"version": 1},
    )
    return 0


def _hook_stop(stdin: dict) -> int:
    """``tombstone hook stop``: record the newest closed turn (R2.1, R4.2).

    Resolves the active session and writes a tombstone record for the newest
    closed turn that is not yet recorded. An error never fails the agent: it
    is logged and the hook exits 0 (design: hooks fail open).
    """
    from . import tombstones

    project = _hook_project_root(stdin)
    if project is None:
        return 0

    tombstones.on_turn_end(project, stdin)
    return 0


def _hook_prompt(stdin: dict) -> int:
    """``tombstone hook prompt``: name the turn and reset its checked list.

    Stores the first 8 words of the prompt (home paths stripped) and clears
    ``state.turn.checked`` so a turn with no checkbox edit is recorded as
    ``unknown`` with a readable name (R2.3, R2.4). Fails open.
    """
    from . import tombstones

    project = _hook_project_root(stdin)
    if project is None:
        return 0

    state_path = project / tombstones.TOMBSTONE_DIR / "state.json"
    tombstones.update_json(
        state_path,
        lambda state: tombstones.rebaseline_snapshots(
            tombstones.record_prompt(state, stdin), project
        ),
        default={"version": 1},
    )
    return 0


def _hook_reconcile(args: argparse.Namespace) -> int:
    """``tombstone hook reconcile``: fill actual for a stored turn (Bug 1).

    Started detached by the Stop hook. Resolves the project, polls the session
    until the turn is priced, then fills ``actual`` and ``requests`` through the
    locked store. Reads its arguments, not STDIN, so it works after the parent
    hook has returned. Fails open: any error exits 0.
    """
    from . import tombstones

    project_arg = getattr(args, "project", None)
    project = Path(project_arg).expanduser() if project_arg \
        else find_project_root()
    if project is None:
        return 0

    kiro_home = Path(args.kiro_home).expanduser() if getattr(args, "kiro_home", None) \
        else None
    session_id = getattr(args, "session", None) or None
    turn_id = getattr(args, "turn", None) or "latest"

    # The child outlives the hook runner: ignore a hangup from a dying
    # terminal or process group and work from the project root.
    import os
    import signal

    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    except (ValueError, OSError, AttributeError):
        pass
    try:
        os.chdir(project)
    except OSError:
        pass

    timeout = getattr(args, "timeout", None)
    extra = {"timeout_s": float(timeout)} if timeout is not None else {}
    tombstones.reconcile_turn(
        project, session_id, turn_id, kiro_home=kiro_home, **extra
    )
    return 0


def _hook_session_start(stdin: dict) -> int:
    """``tombstone hook session-start``: re-baseline the tasks snapshots.

    The safety net runs in ``cmd_hook``. Hand ticks made before the session
    are not counted for the first turn. Fails open.
    """
    from . import tombstones

    project = _hook_project_root(stdin)
    if project is None:
        return 0

    state_path = project / tombstones.TOMBSTONE_DIR / "state.json"
    tombstones.update_json(
        state_path,
        lambda state: tombstones.rebaseline_snapshots(state, project),
        default={"version": 1},
    )
    return 0


def _hook_pre_tool(stdin: dict) -> int:
    """``tombstone hook pre-tool``, cap path (R3.3, R3.4, R11.5).

    Exits 2 with a one-line reason on stderr when a new task would break the
    spec budget, else 0. The STOP file check runs first (R6.2). Fails open:
    any internal error is logged by ``cmd_hook`` and the tool call proceeds.
    Imports stay lazy so the common path is fast.
    """
    project = _hook_project_root(stdin)
    if project is None:
        return 0

    from . import stop

    blocked = stop.check_stop(project)
    if blocked:
        print(blocked, file=sys.stderr)
        return 2

    from . import caps

    message = caps.run_gate(project, stdin)
    if message:
        print(message, file=sys.stderr)
        return 2
    return 0


_HOOK_EVENTS = {
    "pre-tool": _hook_pre_tool,
    "post-tool": _hook_post_tool,
    "prompt": _hook_prompt,
    "stop": _hook_stop,
    "session-start": _hook_session_start,
}

# Hook entries that first fill records left at actual null. pre-tool is left
# out on purpose: it is the latency-critical gate.
_SAFETY_NET_EVENTS = frozenset({"post-tool", "prompt", "stop", "session-start"})

# Hook entries whose STDIN session id is stored for the watcher.
_SESSION_ID_EVENTS = frozenset({"pre-tool", "post-tool", "prompt", "stop"})


def _reconcile_pending_quietly(project: Optional[Path],
                               kiro_home: Optional[Path] = None) -> None:
    """Run the safety net for ``project``. Never raises, never prints."""
    if project is None:
        return
    try:
        from . import tombstones

        tombstones.reconcile_pending(project, kiro_home=kiro_home)
    except Exception:  # noqa: BLE001 - the net must never break a command
        pass


def cmd_hook(args: argparse.Namespace) -> int:
    """Dispatch a hook event, honouring ``--project`` for every event."""
    global _PROJECT_OVERRIDE
    project_arg = getattr(args, "project", None)
    previous = _PROJECT_OVERRIDE
    _PROJECT_OVERRIDE = Path(project_arg).expanduser() if project_arg else None
    try:
        return _dispatch_hook(args)
    finally:
        _PROJECT_OVERRIDE = previous


def _dispatch_hook(args: argparse.Namespace) -> int:
    """Run one hook event. Fail open: an internal error exits 0 (design)."""
    event = args.event

    # reconcile reads CLI arguments, not STDIN, so it is dispatched apart from
    # the STDIN-driven hook handlers (Bug 1).
    if event == "reconcile":
        try:
            return _hook_reconcile(args)
        except Exception as error:  # noqa: BLE001 - hooks never crash the agent
            _log_hook_error({}, f"hook reconcile: {error}")
            return 0

    handler = _HOOK_EVENTS.get(event)
    if handler is None:
        print(f"tombstone hook {event}: not implemented yet", file=sys.stderr)
        return 0

    stdin = _read_stdin_json()
    if event in _SESSION_ID_EVENTS:
        try:
            from . import tombstones

            project = _hook_project_root(stdin)
            if project is not None:
                tombstones.remember_session(project, stdin)
        except Exception as error:  # noqa: BLE001 - fail open
            _log_hook_error(stdin, f"hook {event} session id: {error}")
    if event in _SAFETY_NET_EVENTS:
        _reconcile_pending_quietly(_hook_project_root(stdin))
    try:
        return handler(stdin)
    except Exception as error:  # noqa: BLE001 - hooks must never crash the agent
        _log_hook_error(stdin, f"hook {event}: {error}")
        return 0


def _log_hook_error(stdin: dict, message: str) -> None:
    """Append a hook error to ``errors.log`` under the project. Never raises."""
    try:
        from datetime import datetime, timezone

        project = _hook_project_root(stdin)
        if project is None:
            return
        from . import tombstones

        from .safepath import append_nofollow, guard

        sidecar = project / tombstones.TOMBSTONE_DIR
        if not guard(project, sidecar / "errors.log"):
            return
        sidecar.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        append_nofollow(sidecar / "errors.log", f"{stamp} {message}\n")
    except Exception:  # noqa: BLE001 - logging must never raise
        pass


# --- not-yet-built subcommands ---------------------------------------------


def _not_ready(name: str):
    def runner(_args: argparse.Namespace) -> int:
        print(f"tombstone {name}: not implemented yet", file=sys.stderr)
        return 2
    return runner


def cmd_graveyard(args: argparse.Namespace) -> int:
    """``tombstone graveyard [--out FILE]``: render the static page (R2.6).

    Fills records still at ``actual`` null first, then writes the HTML to
    ``.tombstone/graveyard.html`` or to ``--out``. Reads
    ``tombstones.json`` without modifying it.
    """
    project = Path(args.project).expanduser() if getattr(args, "project", None) \
        else find_project_root()
    if project is None:
        print("no project found (no .kiro directory or tombstone.toml above here)",
              file=sys.stderr)
        return 1

    from . import tombstones

    try:
        loaded = tombstones.load_includes(getattr(args, "include", None) or [])
    except tombstones.IncludeError as exc:
        print(f"tombstone graveyard: {exc}", file=sys.stderr)
        return 1

    _reconcile_pending_quietly(project)

    out = Path(args.out) if args.out else None
    try:
        target = tombstones.write_graveyard(
            project, out, loaded, getattr(args, "autopsies", None) or [],
            getattr(args, "video", None) or None, getattr(args, "repo", None) or None)
    except OSError as exc:
        print(f"tombstone graveyard: could not write: {exc}", file=sys.stderr)
        return 1
    own = tombstones.load_tombstones(project / "tombstones.json")["tombstones"]
    count = len(tombstones.merge_includes(own, loaded)[0])
    print(f"graveyard written: {target} ({count} tombstones)")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """``tombstone resume``: delete the STOP file (R6.6)."""
    project = Path(args.project).expanduser() if getattr(args, "project", None) \
        else find_project_root()
    if project is None:
        print("no project found (no .kiro directory or tombstone.toml above here)",
              file=sys.stderr)
        return 1

    from . import stop

    if stop.resume(project):
        print("tombstone resume: STOP removed, the agent is unblocked")
    else:
        print("tombstone resume: no STOP file, nothing to do")
    return 0


# --- tombstone watch --------------------------------------------------------


def cmd_watch(args: argparse.Namespace) -> int:
    """``tombstone watch``: poll the active session every 2 seconds (R5.1).

    Runs until interrupted. Errors in a poll are logged to ``errors.log`` and
    the loop continues. Spends no credits and makes no network call.
    """
    project = Path(args.project).expanduser() if getattr(args, "project", None) \
        else find_project_root()
    if project is None:
        print("no project found (no .kiro directory or tombstone.toml above here)",
              file=sys.stderr)
        return 1
    kiro_home = Path(args.kiro_home).expanduser() if getattr(args, "kiro_home", None) \
        else None

    from . import watcher

    warning = watcher.check_cli_version(project)
    if warning:
        print(warning, file=sys.stderr)
    print(f"tombstone watch: polling {project} every {watcher.POLL_SECONDS:g} s",
          file=sys.stderr)
    try:
        watcher.run(project, kiro_home=kiro_home)
    except KeyboardInterrupt:
        return 0
    return 0


# --- tombstone mcp ----------------------------------------------------------


def cmd_mcp(args: argparse.Namespace) -> int:
    """``tombstone mcp``: serve ``get_budget`` over stdio (R8.1)."""
    project = Path(args.project).expanduser() if getattr(args, "project", None) \
        else find_project_root()

    from . import mcp_server

    try:
        mcp_server.serve(project)
    except KeyboardInterrupt:
        return 0
    return 0


# --- argument parser --------------------------------------------------------


# --- init builders (task 10.1) ----------------------------------------------
#
# Pure functions: they return the text or data that `tombstone init` writes.
# Only shell `command` actions are produced (R11.1) and no `model` key is ever
# written (R11.3).

_HOOK_TIMEOUT_SECONDS = 10

# (file stem, trigger, hook event, description)
_V3_HOOKS: tuple[tuple[str, str, str, str], ...] = (
    ("pre-tool", "PreToolUse", "pre-tool",
     "Tombstone: block tools when the STOP file exists or a cap is breached"),
    ("post-tool", "PostToolUse", "post-tool",
     "Tombstone: track tasks.md checkbox edits"),
    ("stop", "Stop", "stop",
     "Tombstone: write the tombstone for the finished turn"),
    ("session-start", "SessionStart", "session-start",
     "Tombstone: remember the session id"),
    ("prompt", "UserPromptSubmit", "prompt",
     "Tombstone: remember the prompt so a turn without a task gets a readable name"),
    ("task-pre", "PreTaskExecution", "task-pre",
     "Tombstone: store the task id when a spec task starts"),
    ("task-post", "PostTaskExecution", "task-post",
     "Tombstone: note that a spec task ended"),
)

# V2 camelCase keys and the hook event each one calls.
_V2_HOOKS: tuple[tuple[str, str], ...] = (
    ("preToolUse", "pre-tool"),
    ("postToolUse", "post-tool"),
    ("stop", "stop"),
)


def find_executable() -> str:
    """Absolute path of the interpreter that runs ``tombstone``.

    Hooks and the MCP server call ``<python> -m tombstone ...`` so they work
    when the virtualenv holding tombstone is not on PATH.
    """
    return sys.executable or "python3"


def _sh_quote(text: str) -> str:
    """Always single-quote ``text`` for ``sh`` (paths may hold spaces)."""
    return "'" + str(text).replace("'", "'\"'\"'") + "'"


def hook_command(python: str, event: str, project: Path) -> str:
    """The shell command of one hook: quoted interpreter, ``-m tombstone``."""
    return (f"{_sh_quote(python)} -m tombstone hook {event} "
            f"--project {_sh_quote(str(Path(project).resolve()))}")


def build_mcp_entry(python: str, project: Path) -> dict:
    """The ``tombstone`` entry of ``mcpServers`` in ``.kiro/settings/mcp.json``."""
    return {"command": str(python),
            "args": ["-m", "tombstone", "mcp", "--project",
                     str(Path(project).resolve())]}


def merge_mcp_settings(path: Path, python: str, project: Path) -> str:
    """Add the ``tombstone`` server to the settings file. Returns the report line.

    Keeps every other key and server. An existing ``tombstone`` entry, an
    unreadable file or a non-object ``mcpServers`` is left as it is (R9.4).
    """
    import json

    if not is_safe_target(project, path):
        return f"{REFUSED} {path}"
    entry = build_mcp_entry(python, project)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_atomic(path, _json_text({"mcpServers": {"tombstone": entry}}))
        except OSError:
            return f"{REFUSED} {path}"
        return f"created {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"exists, not valid JSON, left unchanged {path}; add the tombstone server by hand"
    if not isinstance(data, dict):
        return f"exists, not a JSON object, left unchanged {path}; add the tombstone server by hand"
    servers = data.get("mcpServers")
    if servers is None:
        servers = data["mcpServers"] = {}
    if not isinstance(servers, dict):
        return f"exists, mcpServers is not an object, left unchanged {path}"
    if "tombstone" in servers:
        return f"exists, tombstone server already registered, left unchanged {path}"
    servers["tombstone"] = entry
    try:
        write_atomic(path, _json_text(data))
    except OSError:
        return f"{REFUSED} {path}"
    return f"updated {path} (added the tombstone server, kept the rest)"


def build_v3_hook_files(executable: str, project: Path) -> dict[str, dict]:
    """V3 hook files keyed by file name (``tombstone-<name>.json``)."""
    files: dict[str, dict] = {}
    for stem, trigger, event, description in _V3_HOOKS:
        files[f"tombstone-{stem}.json"] = {
            "version": "v1",
            "hooks": [{
                "name": f"tombstone-{stem}",
                "description": description,
                "trigger": trigger,
                "action": {
                    "type": "command",
                    "command": hook_command(executable, event, project),
                    "timeout": _HOOK_TIMEOUT_SECONDS,
                },
            }],
        }
    return files


def build_v2_agent_config(executable: str, project: Path) -> dict:
    """The V2 agent config for ``.kiro/agents/tombstone.json``. No model key."""
    hooks = {
        key: [{"command": hook_command(executable, event, project),
               "timeout_ms": _HOOK_TIMEOUT_SECONDS * 1000}]
        for key, event in _V2_HOOKS
    }
    return {
        "name": "tombstone",
        "description": "Tombstone circuit breaker hooks (no model override)",
        "hooks": hooks,
    }


def build_steering_text() -> str:
    """Steering file text for ``.kiro/steering/tombstone.md``."""
    return """---
inclusion: always
---

# Budget mode (Tombstone)

This project runs under Tombstone, a circuit breaker that caps credits per task and per spec. The cap is public: the numbers are in `tombstone.toml` and in the `get_budget` answer, and you are expected to plan within them.

## Before each spec task

1. Call the MCP tool `get_budget` with the task name.
2. Read the three numbers: similar task cost, remaining spec budget, plan balance.
3. If the remaining spec budget is lower than the similar task cost, do not start. Tell the human the three numbers and ask how to proceed.
4. If the plan balance is null or older than 30 minutes, say so in one line and continue.

## While working

- Keep the model you were given. Never switch, suggest or request another model to save credits.
- Prefer one focused edit over re-reading the same files. If you have read a file twice without changing anything, stop and say what is blocking you.
- If a tool call is refused with a message starting `TOMBSTONE:`, that means stop. Stop immediately. Do not retry the tool, do not try another tool to get around it, do not edit the cap. Quote the message to the human and wait.
- Do not edit or delete anything in `.tombstone/`, `tombstone.toml` or `tombstones.json` unless the human asks for it.

## After a task

- Mention the task id so the tombstone gets the right name.
"""


def build_default_toml() -> str:
    """Default ``tombstone.toml``; uses exactly the keys ``load_config`` reads."""
    return """# Tombstone configuration. All numbers are credits unless stated.

[caps]
# Hard cap per task and per spec, in credits.
task = 40
spec = 300

[stall]
# Minutes of model calls with no project file change before a stall is declared.
quiet_window_minutes = 10

[budget]
# Estimated price of one request (measured on Kiro CLI 3.0.0). Used for est. only.
avg_request_price = 0.25
# Warn when the plan balance is below this many credits.
min_plan_balance = 0

[stop]
# Seconds between the STOP file and killing the agent process.
kill_grace_seconds = 30
"""


# --- tombstone init (task 10.2) ---------------------------------------------


def _json_text(data: object) -> str:
    import json

    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


from .safepath import REFUSED, is_safe_target, write_atomic  # noqa: E402


def _write_if_absent(path: Path, text: str, project: Optional[Path] = None) -> str:
    """Create ``path`` only when absent (R9.4). Returns the printed line.

    With ``project`` the path must pass ``is_safe_target`` first.
    """
    if project is not None and not is_safe_target(project, path):
        return f"{REFUSED} {path}"
    if path.exists():
        return f"exists, left unchanged {path}"
    try:
        write_atomic(path, text)
    except OSError:
        return f"{REFUSED} {path}"
    return f"created {path}"


def unit_id(project: Path) -> str:
    """``<name>-<hash8>``: project name plus a short hash of its path."""
    import hashlib
    import re

    resolved = Path(project).resolve()
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", resolved.name).strip("-") or "project"
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
    return f"{name}-{digest}"


def build_launchd_plist(executable: str, project: Path, label: str) -> str:
    """launchd plist text for the watcher (RunAtLoad, KeepAlive)."""
    import plistlib

    data = {
        "Label": label,
        "ProgramArguments": [str(executable), "-m", "tombstone", "watch",
                             "--project", str(Path(project).resolve())],
        "RunAtLoad": True,
        "KeepAlive": True,
    }
    return plistlib.dumps(data).decode("utf-8")


def _systemd_quote(arg: str) -> str:
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def build_systemd_unit(executable: str, project: Path) -> str:
    """systemd user unit text for the watcher."""
    exec_start = " ".join(_systemd_quote(a) for a in (
        str(executable), "-m", "tombstone", "watch", "--project",
        str(Path(project).resolve())))
    return (
        "[Unit]\n"
        "Description=Tombstone watcher\n"
        "\n"
        "[Service]\n"
        f"ExecStart={exec_start}\n"
        "Restart=always\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _run_load_command(command: list[str]) -> tuple[int, str]:
    """Run the unit load command. The only place init calls launchctl or
    systemctl (steering allows it for init only)."""
    import subprocess

    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return done.returncode, (done.stderr or done.stdout or "").strip()


def cmd_init(args: argparse.Namespace) -> int:
    """``tombstone init``: create project files and the watcher unit."""
    import os
    import shlex

    project = (Path(args.project).expanduser() if getattr(args, "project", None)
               else Path.cwd()).resolve()
    if not project.is_dir():
        print(f"tombstone init: not a directory: {project}", file=sys.stderr)
        return 1
    executable = find_executable()
    lines: list[str] = []
    recorded: dict[str, str] = {}

    def create(path: Path, text: str, key: str, guard: bool = True) -> None:
        line = _write_if_absent(path, text, project if guard else None)
        lines.append(line)
        if line.startswith("created"):
            recorded[key] = _sha_text(text)

    for name, data in build_v3_hook_files(executable, project).items():
        create(project / ".kiro" / "hooks" / name, _json_text(data),
               f".kiro/hooks/{name}")
    v2 = bool(getattr(args, "v2", False))
    agent_path = project / ".kiro" / "agents" / "tombstone.json"
    if v2:
        create(agent_path, _json_text(build_v2_agent_config(executable, project)),
               ".kiro/agents/tombstone.json")
    elif agent_path.exists():
        lines.append(f"note: {agent_path} exists. Kiro CLI 3.1.0 loads it as the "
                     "active profile and that profile has no MCP tools. Remove "
                     "it yourself if you use V3 (init never deletes files).")
    mcp_line = merge_mcp_settings(
        project / ".kiro" / "settings" / "mcp.json", executable, project)
    lines.append(mcp_line)
    if mcp_line.startswith(("created", "updated")):
        recorded["mcp-entry"] = _sha_entry(build_mcp_entry(executable, project))
    create(project / ".kiro" / "steering" / "tombstone.md", build_steering_text(),
           ".kiro/steering/tombstone.md")
    create(project / "tombstone.toml", build_default_toml(), "tombstone.toml")

    watch_cmd = (f"{shlex.quote(executable)} -m tombstone watch "
                 f"--project {shlex.quote(str(project))}")
    failed = False
    if getattr(args, "no_watcher_unit", False):
        lines.append("watcher unit skipped (--no-watcher-unit)")
        lines.append("run the watcher by hand:")
        lines.append(f"  {watch_cmd}")
    else:
        uid_name = unit_id(project)
        home = Path.home()
        if sys.platform == "darwin":
            label = f"com.tombstone.{uid_name}"
            unit_path = home / "Library" / "LaunchAgents" / f"{label}.plist"
            text = build_launchd_plist(executable, project, label)
            load = ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(unit_path)]
        else:
            unit_path = home / ".config" / "systemd" / "user" / f"tombstone-{uid_name}.service"
            text = build_systemd_unit(executable, project)
            load = ["systemctl", "--user", "enable", "--now", unit_path.name]
        create(unit_path, text, "unit", guard=False)
        load_text = " ".join(shlex.quote(p) for p in load)
        code, detail = _run_load_command(load)
        if code == 0:
            lines.append(f"loaded watcher unit: {load_text}")
        else:
            failed = True
            lines.append(f"could not load the watcher unit ({detail or 'exit ' + str(code)})")
            lines.append(f"load it yourself: {load_text}")
            lines.append(f"or run by hand: {watch_cmd}")

    _record_install(project, recorded)

    from . import watcher

    version_warning = watcher.check_cli_version(project)
    if version_warning:
        lines.append(version_warning)

    lines.append("")
    lines.append("Runtime state lives in .tombstone/. Add `.tombstone/` to your .gitignore;")
    lines.append("do not ignore .kiro/ (Tombstone never writes it there).")
    lines.append("")
    lines.append("Start Kiro with the hooks active:")
    lines.append("  V3 (default):  kiro-cli chat            hooks in .kiro/hooks are active,")
    lines.append("                                          get_budget comes from .kiro/settings/mcp.json")
    if v2:
        lines.append("  V2 (legacy):   kiro-cli chat --agent tombstone")
    else:
        lines.append("  V2 (legacy):   run `tombstone init --v2` to write .kiro/agents/tombstone.json")
    print("\n".join(lines))
    refused = any(line.startswith(REFUSED) for line in lines)
    return 1 if failed or refused else 0


# --- install record and uninstall (task 10.4) --------------------------------

INSTALLED_FILE = SIDECAR / "installed.json"
LEGACY_INSTALLED_FILE = LEGACY / "installed.json"


def _sha_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha_file(path: Path) -> Optional[str]:
    import hashlib

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _sha_entry(entry: dict) -> str:
    import json

    return _sha_text(json.dumps(entry, sort_keys=True))


def _state_dir() -> Path:
    """User state dir for Tombstone (XDG_STATE_HOME honoured)."""
    import os

    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base and Path(base).is_absolute() \
        else Path.home() / ".local" / "state"
    return root / "tombstone"


def install_record_path(project: Path) -> Path:
    """Install record kept outside the repo, keyed by the init unit id."""
    return _state_dir() / "installs" / f"{unit_id(project)}.json"


def _read_files_map(path: Path) -> dict[str, str]:
    import json

    try:
        files = json.loads(path.read_text(encoding="utf-8")).get("files")
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(files, dict):
        return {}
    return {str(k): str(v) for k, v in files.items()}


def _load_installed(project: Path) -> dict[str, str]:
    """The trusted ``files`` map from the record outside the repo; {} if none.

    The in-repo ``installed.json`` (``.tombstone/`` or the old ``.kiro/tombstone/``) can be
    forged by a cloned repository, so it is never read for decisions.
    """
    return _read_files_map(install_record_path(project))


def _save_installed(project: Path, files: dict[str, str]) -> None:
    path = install_record_path(project)
    write_atomic(path, _json_text({"version": 1, "files": files}))


def _record_install(project: Path, created: dict[str, str]) -> None:
    """Store the sha256 of each file init created (merged with older records)."""
    if not created:
        return
    try:
        files = _load_installed(project)
        files.update(created)
        _save_installed(project, files)
    except OSError:
        pass


def _unit_target(project: Path, home: Path) -> tuple[Path, list[str]]:
    """Unit file init derives for this project, and the unload command."""
    import os

    uid_name = unit_id(project)
    if sys.platform == "darwin":
        label = f"com.tombstone.{uid_name}"
        path = home / "Library" / "LaunchAgents" / f"{label}.plist"
        return path, ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"]
    path = home / ".config" / "systemd" / "user" / f"tombstone-{uid_name}.service"
    return path, ["systemctl", "--user", "disable", "--now", path.name]


def uninstall(project: Path, home: Path, purge: bool = False,
              force: bool = False) -> list[str]:
    """Undo ``init`` for one project. Returns one report line per file.

    A file counts as changed when its sha256 differs from the one stored in
    ``installed.json`` or, without a record, from what the builders produce
    today. Changed files are kept unless ``force``. Safe to run twice.
    """
    import json
    import os

    project = Path(project).resolve()
    executable = find_executable()
    recorded = _load_installed(project)
    lines: list[str] = []
    gone: set[str] = set()

    def expected(key: str, text: Optional[str]) -> Optional[str]:
        return recorded.get(key) or (_sha_text(text) if text is not None else None)

    def remove(path: Path, key: str, text: Optional[str],
               guard: bool = True) -> None:
        if guard and not is_safe_target(project, path):
            lines.append(f"{REFUSED} {path}")
            return
        if not path.exists():
            lines.append(f"not found {path}")
            gone.add(key)
            return
        want = expected(key, text)
        if not force and _sha_file(path) != want:
            lines.append(f"kept {path} (changed since init, use --force to remove)")
            return
        try:
            path.unlink()
        except OSError as exc:
            lines.append(f"kept {path} (could not remove: {exc})")
            return
        lines.append(f"removed {path}")
        gone.add(key)

    # 1. watcher unit: unload, then delete only the unit init derives here.
    unit_path, unload = _unit_target(project, home)
    if unit_path.exists():
        code, detail = _run_load_command(unload)
        if code != 0:
            lines.append(f"note: unload returned {detail or 'exit ' + str(code)} "
                         f"({' '.join(unload)}); the unit may not have been loaded")
        text = (build_launchd_plist(executable, project, f"com.tombstone.{unit_id(project)}")
                if sys.platform == "darwin" else build_systemd_unit(executable, project))
        remove(unit_path, "unit", text, guard=False)
    else:
        lines.append(f"not found {unit_path}")
        gone.add("unit")

    # 2. hook files, agent config, steering.
    hooks_dir = project / ".kiro" / "hooks"
    builders = build_v3_hook_files(executable, project)
    names = set(builders)
    try:
        if is_safe_target(project, hooks_dir):
            names.update(p.name for p in hooks_dir.glob("tombstone-*.json"))
    except OSError:
        pass
    for name in sorted(names):
        data = builders.get(name)
        remove(hooks_dir / name, f".kiro/hooks/{name}",
               _json_text(data) if data is not None else None)
    remove(project / ".kiro" / "agents" / "tombstone.json",
           ".kiro/agents/tombstone.json",
           _json_text(build_v2_agent_config(executable, project)))
    remove(project / ".kiro" / "steering" / "tombstone.md",
           ".kiro/steering/tombstone.md", build_steering_text())

    # 3. the tombstone entry of mcp.json, nothing else.
    mcp_path = project / ".kiro" / "settings" / "mcp.json"
    mcp_safe = is_safe_target(project, mcp_path)
    if not mcp_safe:
        lines.append(f"{REFUSED} {mcp_path}")
        data = False
    else:
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8")) if mcp_path.exists() else None
        except (OSError, ValueError):
            lines.append(f"kept {mcp_path} (not valid JSON, remove the tombstone server by hand)")
            data = False
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if data is None:
        lines.append(f"not found {mcp_path}")
        gone.add("mcp-entry")
    elif data is False:
        pass
    elif not isinstance(servers, dict) or "tombstone" not in servers:
        lines.append(f"not found tombstone entry in {mcp_path}")
        gone.add("mcp-entry")
    else:
        entry = servers["tombstone"]
        want = recorded.get("mcp-entry") or _sha_entry(build_mcp_entry(executable, project))
        if not force and not (isinstance(entry, dict) and _sha_entry(entry) == want):
            lines.append(f"kept tombstone entry in {mcp_path} (changed since init, use --force to remove)")
        else:
            del servers["tombstone"]
            if not servers:
                del data["mcpServers"]
            if data:
                try:
                    write_atomic(mcp_path, _json_text(data))
                except OSError:
                    lines.append(f"{REFUSED} {mcp_path}")
                else:
                    lines.append(f"removed tombstone entry from {mcp_path} (other settings kept)")
                    gone.add("mcp-entry")
            else:
                mcp_path.unlink()
                lines.append(f"removed {mcp_path} (nothing else was in it)")
                gone.add("mcp-entry")

    # 4. user data, and --purge.
    migrate_sidecar(project)
    sidecar = project / SIDECAR
    toml_path = project / "tombstone.toml"
    if purge:
        remove(toml_path, "tombstone.toml", build_default_toml())
    elif toml_path.exists():
        lines.append(f"kept {toml_path} (user data, --purge removes it)")
    else:
        lines.append(f"not found {toml_path}")
    ledger = project / "tombstones.json"
    lines.append(f"kept {ledger} (user data, never removed)" if ledger.exists()
                 else f"not found {ledger}")
    if purge:
        if not is_safe_target(project, sidecar):
            lines.append(f"{REFUSED} {sidecar}")
        elif sidecar.exists():
            lines.extend(_purge_tree(project, sidecar))
        else:
            lines.append(f"not found {sidecar}")
    else:
        for item in (sidecar / "autopsy", sidecar / "graveyard.html"):
            if is_safe_target(project, item) and item.exists():
                lines.append(f"kept {item} (user data, --purge removes it)")
        # legacy in-repo installed.json: never trusted, remove when safe.
        for legacy in (project / INSTALLED_FILE, project / LEGACY_INSTALLED_FILE):
            if is_safe_target(project, legacy):
                try:
                    legacy.unlink()
                except OSError:
                    pass
            elif os.path.lexists(legacy) or sidecar.is_symlink() \
                    or (project / ".kiro").is_symlink():
                lines.append(f"{REFUSED} {legacy}")
    # outside install record: drop entries of removed files, delete when empty.
    record = install_record_path(project)
    if record.exists():
        left = {k: v for k, v in recorded.items() if k not in gone}
        try:
            if left:
                _save_installed(project, left)
            else:
                record.unlink()
        except OSError:
            pass
    return lines


def _purge_tree(project: Path, root: Path) -> list[str]:
    """Delete regular files and empty dirs under ``root`` (``--purge``).

    Never follows or removes a symlink and never calls rmtree. Anything that
    fails ``is_safe_target`` is reported as refused and left alone.
    """
    import os
    import stat

    lines: list[str] = []
    for current, dirnames, filenames in os.walk(root, topdown=False,
                                                followlinks=False):
        here = Path(current)
        for name in filenames:
            path = here / name
            try:
                regular = stat.S_ISREG(os.lstat(path).st_mode)
            except OSError:
                continue
            if not regular or not is_safe_target(project, path):
                lines.append(f"{REFUSED} {path}")
                continue
            try:
                path.unlink()
            except OSError:
                pass
        for name in dirnames:
            path = here / name
            if path.is_symlink() or not is_safe_target(project, path):
                lines.append(f"{REFUSED} {path}")
                continue
            try:
                path.rmdir()
            except OSError:
                pass
    try:
        root.rmdir()
        lines.append(f"removed {root}")
    except OSError:
        lines.append(f"kept {root} (not empty)")
    return lines


def cmd_uninstall(args: argparse.Namespace) -> int:
    """``tombstone uninstall [--purge] [--force] [--project DIR]``."""
    project = (Path(args.project).expanduser() if getattr(args, "project", None)
               else Path.cwd()).resolve()
    if not project.is_dir():
        print(f"tombstone uninstall: not a directory: {project}", file=sys.stderr)
        return 1
    lines = uninstall(project, Path.home(), purge=bool(args.purge),
                      force=bool(args.force))
    for line in lines:
        print(line)
    return 1 if any(line.startswith(REFUSED) for line in lines) else 0


# --- Power folder (task 10.3) -----------------------------------------------

_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
_MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
_REPO_URL = "https://github.com/DmitriyTopka/tombstone"
POWER_PROJECT_PLACEHOLDER = "/path/to/project"
POWER_PYTHON_PLACEHOLDER = "/path/to/python"


def build_plugin_json() -> dict:
    """``power/plugin.json`` (Agent Plugins spec)."""
    from . import __version__

    return {
        "$schema": _PLUGIN_SCHEMA,
        "name": "tombstone",
        "version": __version__,
        "description": ("Circuit breaker for Kiro specs: prices tasks in "
                        "credits, enforces caps, stops stuck agents."),
        "author": {"name": "DmitriyTopka", "url": "https://github.com/DmitriyTopka"},
        "keywords": ["kiro", "budget", "credits", "circuit-breaker", "mcp"],
        "homepage": _REPO_URL,
        "repository": _REPO_URL,
        "license": "MIT",
    }


def build_power_mcp_json() -> dict:
    """``power/mcp.json``: the stdio server for ``get_budget``."""
    return {
        "$schema": _MCP_SCHEMA,
        "mcpServers": {
            "tombstone": {"type": "stdio", "command": POWER_PYTHON_PLACEHOLDER,
                          "args": ["-m", "tombstone", "mcp"]},
        },
    }


def build_skill_text() -> str:
    """``power/skills/tombstone/SKILL.md``."""
    return """---
name: tombstone
description: Plan within a credit budget. Call get_budget before each spec task and treat a TOMBSTONE: refusal as a hard stop.
---

# Tombstone budget skill

Tombstone is a circuit breaker that caps credits per task and per spec. The caps are public, in `tombstone.toml` and in the `get_budget` answer.

## get_budget

Before each spec task, call the MCP tool `get_budget` with the task name. It returns three groups of numbers, all in credits:

- `similar`: the median cost of similar finished tasks, with `basis` (`actual` or `est.`), plus `task_cap`.
- `spec`: the spec `cap`, `spent`, `remaining` and `basis`.
- `plan_balance`: the plan `value` (actual), `age_minutes` and a `reason` when it is unavailable.

If `spec.remaining` is lower than `similar.median`, do not start the task. Tell the human the numbers and ask how to proceed. If `plan_balance.value` is null or older than 30 minutes, say so in one line and continue. Keep the model you were given and never suggest another one to save credits.

## A TOMBSTONE: refusal

A tool call refused with a message that starts with `TOMBSTONE:` means stop. Do not retry the tool, do not try another tool to get around it, and do not edit the cap or anything in `.tombstone/`, `tombstone.toml` or `tombstones.json`. Quote the message to the human and wait. The human removes the block with `tombstone resume`.

## Hooks are not part of the power

Hooks are not a documented power component. The files in `power/hooks/` (`v3/*.json` and `v2/tombstone.json`) are reference templates generated from the same builders as `tombstone init`, with placeholder paths (`/path/to/python`, `/path/to/project`). `tombstone init` installs the real hook files into `.kiro/hooks/` and registers the MCP server in `.kiro/settings/mcp.json` of the project, both with the absolute interpreter path. It writes `.kiro/agents/tombstone.json` only with `--v2`, because Kiro CLI 3.1.0 loads that file as the active profile and that profile has no MCP tools. Installing the power alone does not enable the cap gate.
"""


def build_power_files(root: Path) -> dict[Path, str]:
    """Every file of the ``power/`` folder, keyed by path under ``root``.

    Hook templates come from the same builders ``init`` uses, with a
    placeholder interpreter and project path.
    """
    root = Path(root)
    project = Path(POWER_PROJECT_PLACEHOLDER)
    python = POWER_PYTHON_PLACEHOLDER
    files: dict[Path, str] = {
        root / "plugin.json": _json_text(build_plugin_json()),
        root / "mcp.json": _json_text(build_power_mcp_json()),
        root / "skills" / "tombstone" / "SKILL.md": build_skill_text(),
        root / "hooks" / "v2" / "tombstone.json": _json_text(
            build_v2_agent_config(python, project)),
    }
    for name, data in build_v3_hook_files(python, project).items():
        files[root / "hooks" / "v3" / name] = _json_text(data)
    return files


def write_power_folder(root: Path) -> list[Path]:
    """Write the power folder, replacing generated files. Returns the paths."""
    written: list[Path] = []
    for path, text in build_power_files(root).items():
        write_atomic(path, text)
        written.append(path)
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tombstone",
        description="External circuit breaker for Kiro specs.",
    )
    from . import __version__
    parser.add_argument("--version", action="version",
                        version=f"tombstone {__version__}")
    sub = parser.add_subparsers(dest="command")

    cost = sub.add_parser("cost", help="price one session")
    cost.add_argument("session", help="a session path, a session id, or a unique id prefix")
    cost.add_argument("--kiro-home", default=None,
                      help="override the Kiro home used to resolve ids")
    cost.set_defaults(func=cmd_cost)

    hook = sub.add_parser("hook", help="hook entry points, read STDIN")
    hook.add_argument(
        "event",
        choices=[
            "pre-tool",
            "post-tool",
            "prompt",
            "stop",
            "reconcile",
            "session-start",
            "task-pre",
            "task-post",
        ],
        help="the hook event to handle",
    )
    # reconcile is started detached by the Stop hook and reads these arguments
    # instead of STDIN (Bug 1).
    hook.add_argument("--session", default=None,
                      help="session id for the reconcile child")
    hook.add_argument("--turn", default="latest",
                      help="turn id to reconcile, or 'latest'")
    hook.add_argument("--project", default=None,
                      help="project root for the reconcile child")
    hook.add_argument("--kiro-home", default=None,
                      help="override the Kiro home used to resolve the session")
    hook.add_argument("--timeout", type=float, default=None,
                      help="reconcile polling limit in seconds (default 30)")
    hook.set_defaults(func=cmd_hook)

    graveyard = sub.add_parser("graveyard", help="render the static graveyard page")
    graveyard.add_argument("--out", default=None,
                           help="output file (default .tombstone/graveyard.html)")
    graveyard.add_argument("--include", action="append", default=[], metavar="FILE",
                           help="add the records of another tombstones file (repeatable)")
    graveyard.add_argument("--autopsies", action="append", default=[], metavar="DIR",
                           help="read stop receipts from DIR as well (repeatable)")
    graveyard.add_argument("--video", default=None, metavar="URL",
                           help="link to the demo video, drawn in the page header")
    graveyard.add_argument("--repo", default=None, metavar="URL",
                           help="link to the repository, drawn in the page header")
    graveyard.add_argument("--project", default=None,
                           help="project root (default: found from the working directory)")
    graveyard.set_defaults(func=cmd_graveyard)

    watch = sub.add_parser("watch", help="run the watcher for one project")
    watch.add_argument("--project", default=None,
                       help="project root (default: found from the working directory)")
    watch.add_argument("--kiro-home", default=None,
                       help="override the Kiro home used to find the session")
    watch.set_defaults(func=cmd_watch)

    resume = sub.add_parser("resume", help="remove the STOP file and unblock the agent")
    resume.add_argument("--project", default=None,
                        help="project root (default: found from the working directory)")
    resume.set_defaults(func=cmd_resume)

    mcp_cmd = sub.add_parser("mcp", help="run the stdio MCP server (get_budget)")
    mcp_cmd.add_argument("--project", default=None,
                         help="project root (default: found from the working directory)")
    mcp_cmd.set_defaults(func=cmd_mcp)

    init = sub.add_parser("init", help="create project files and the watcher unit")
    init.add_argument("--project", default=None,
                      help="project root (default: the working directory)")
    init.add_argument("--no-watcher-unit", action="store_true",
                      help="do not write or load the launchd/systemd unit; "
                           "print how to run the watcher by hand")
    init.add_argument("--v2", action="store_true",
                      help="also write the legacy V2 agent config "
                           ".kiro/agents/tombstone.json (Kiro 3.1.0 loads it "
                           "as the active profile, which has no MCP tools)")
    init.set_defaults(func=cmd_init)

    uninstall_cmd = sub.add_parser(
        "uninstall", help="remove what init created (keeps your data)")
    uninstall_cmd.add_argument("--project", default=None,
                               help="project root (default: the working directory)")
    uninstall_cmd.add_argument("--purge", action="store_true",
                               help="also remove .tombstone/ and tombstone.toml "
                                    "(tombstones.json is always kept)")
    uninstall_cmd.add_argument("--force", action="store_true",
                               help="remove files even when changed since init")
    uninstall_cmd.set_defaults(func=cmd_uninstall)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 2
    _migrate_sidecar_quietly(args)
    return args.func(args)


def _migrate_sidecar_quietly(args: argparse.Namespace) -> None:
    """Move an old ``.kiro/tombstone/`` to ``.tombstone/`` once (R12.2).

    Runs before every command that has a project, so the first command that
    touches state does the move. Never raises.
    """
    try:
        given = getattr(args, "project", None)
        project = Path(given).expanduser() if given else find_project_root()
        if project is not None:
            from .sidecar import migrate

            migrate(project)
    except (OSError, ValueError):
        pass


if __name__ == "__main__":
    raise SystemExit(main())
