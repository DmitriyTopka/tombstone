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


# --- not-yet-built subcommands ---------------------------------------------


def _not_ready(name: str):
    def runner(_args: argparse.Namespace) -> int:
        print(f"tombstone {name}: not implemented yet", file=sys.stderr)
        return 2
    return runner


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

    for name in ("hook", "watch", "mcp", "graveyard", "resume", "init"):
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
