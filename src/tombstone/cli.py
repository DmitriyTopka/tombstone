"""Command line entry point for the tombstone command.

``cli.main`` parses arguments with argparse and imports a command's module
only after parsing, so a hook path never pays for ``mcp``, ``subprocess`` or
``tomllib`` it does not use (design: lazy imports per command).

Task 2 lands the entry point and ``tombstone cost``. Other subcommands are
registered as they are built; until then they report that they are not ready.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence


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


def _hook_project_root(stdin: dict) -> Optional[Path]:
    """Find the project root for a hook from STDIN cwd, else the process cwd."""
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
        lambda state: tombstones.record_prompt(state, stdin),
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

    tombstones.reconcile_turn(
        project, session_id, turn_id, kiro_home=kiro_home
    )
    return 0


def _hook_session_start(stdin: dict) -> int:
    """``tombstone hook session-start``: only runs the safety net (cmd_hook)."""
    return 0


def _hook_pre_tool(stdin: dict) -> int:
    """``tombstone hook pre-tool``, cap path (R3.3, R3.4, R11.5).

    Exits 2 with a one-line reason on stderr when a new task would break the
    spec budget, else 0. Fails open: any internal error is logged by
    ``cmd_hook`` and the tool call proceeds. The STOP file check is added in
    task 7.1. Imports stay lazy so the common path is fast.
    """
    project = _hook_project_root(stdin)
    if project is None:
        return 0

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
    """Dispatch a hook event. Fail open: an internal error exits 0 (design)."""
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

        sidecar = project / tombstones.TOMBSTONE_DIR
        sidecar.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        with (sidecar / "errors.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
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
    ``.kiro/tombstone/graveyard.html`` or to ``--out``. Reads
    ``tombstones.json`` without modifying it.
    """
    project = Path(args.project).expanduser() if getattr(args, "project", None) \
        else find_project_root()
    if project is None:
        print("no project found (no .kiro directory or tombstone.toml above here)",
              file=sys.stderr)
        return 1

    _reconcile_pending_quietly(project)

    from . import tombstones

    out = Path(args.out) if args.out else None
    target = tombstones.write_graveyard(project, out)
    count = len(tombstones.load_tombstones(project / "tombstones.json")["tombstones"])
    print(f"graveyard written: {target} ({count} tombstones)")
    return 0


# --- argument parser --------------------------------------------------------


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
    hook.set_defaults(func=cmd_hook)

    graveyard = sub.add_parser("graveyard", help="render the static graveyard page")
    graveyard.add_argument("--out", default=None,
                           help="output file (default .kiro/tombstone/graveyard.html)")
    graveyard.add_argument("--project", default=None,
                           help="project root (default: found from the working directory)")
    graveyard.set_defaults(func=cmd_graveyard)

    for name in ("watch", "mcp", "resume", "init"):
        placeholder = sub.add_parser(name, help=f"{name} (not implemented yet)")
        placeholder.set_defaults(func=_not_ready(name))

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
