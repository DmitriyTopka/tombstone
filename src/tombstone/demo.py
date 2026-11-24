"""``tombstone demo``: watch the breaker trip with no Kiro, no credits (R14).

Replays a recorded, scrubbed Kiro V3 session that ships as package data, one
request at a time, through the watcher's own estimate and over-cap logic
(``watcher.evaluate``). When the estimate passes the cap it calls
``stop.begin_stop`` (STOP file, autopsy, killed record) and then the real
pre-tool hook function. Nothing starts Kiro, calls a model, uses the network
or reads the user's ``~/.kiro``: the session is copied under the scratch
project and used as the Kiro home for the autopsy lookup.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DEMO_TASK_CAP = 0.30
DATA_DIR = Path(__file__).resolve().parent / "data" / "demo_session"
SAMPLE_TOOL_CALL = {
    "hook_event_name": "PreToolUse",
    "tool_name": "fs_write",
    "tool_input": {"path": "demo.txt", "text": "one more edit"},
}


def packaged_session() -> Optional[Path]:
    """The shipped ``sess_*`` directory, or None when the data is missing."""
    if not DATA_DIR.is_dir():
        return None
    found = sorted(p for p in DATA_DIR.iterdir()
                   if p.is_dir() and p.name.startswith("sess_"))
    return found[0] if found else None


def prepare_dir(dir_arg: Optional[str]) -> Path:
    """The scratch project: a new temp dir, or ``--dir`` when empty or absent.

    Raises ``ValueError`` when ``--dir`` is a file or a non-empty directory.
    """
    if dir_arg is None:
        return Path(tempfile.mkdtemp(prefix="tombstone-demo-"))
    target = Path(dir_arg).expanduser()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError(f"{target} is not empty; give an empty or new directory")
    target.mkdir(parents=True, exist_ok=True)
    return target


def run(dir_arg: Optional[str] = None, out=None) -> int:
    """Run the demo. Returns the exit code (0, or 1 on a refused ``--dir``)."""
    import sys

    from . import caps, cli, sessions, watcher
    from .safepath import is_safe_target, write_atomic

    out = out or sys.stdout
    say = lambda text: print(text, file=out)  # noqa: E731

    source = packaged_session()
    if source is None:
        print("tombstone demo: the packaged session is missing", file=sys.stderr)
        return 1
    try:
        project = prepare_dir(dir_arg)
    except (ValueError, OSError) as exc:
        print(f"tombstone demo: {exc}", file=sys.stderr)
        return 1
    project = project.resolve()

    config = project / "tombstone.toml"
    if not is_safe_target(project, config):
        print("tombstone demo: refusing to write through a symlink", file=sys.stderr)
        return 1
    write_atomic(config, f"[caps]\ntask = {DEMO_TASK_CAP:.2f}\n")
    kiro_home = project / ".tombstone" / "demo-kiro"
    session_dir = kiro_home / "sessions" / source.name
    shutil.copytree(source, session_dir)

    cfg = caps.load_config(project)
    session = sessions.parse_session(session_dir)
    total = sessions.assistant_counts(session_dir).get(
        session.turns[0].turn_id, 0) if session.turns else 0
    turn_id = session.turns[0].turn_id if session.turns else "demo-turn"

    say(f"tombstone demo: replaying a recorded Kiro V3 session in {project}")
    say(f"task cap {cfg.task_cap:.2f} credits; every number is est.")

    now = datetime.now(timezone.utc)
    state = watcher.default_state()
    watcher.set_session(state, session.session_id, "v3", session_dir)
    state["turn"] = {"watch_turn_id": turn_id, "started_at": now.isoformat(),
                     "event_raised": False}
    watcher.update_state(project, lambda s: s.update(
        session=state["session"], turn=state["turn"]))

    event = None
    for units in range(1, total + 1):
        obs = watcher.Observation(
            project=str(project), session_path=str(session_dir), engine="v3",
            session_id=session.session_id, turn_id=turn_id, units=units)
        stamp = now + timedelta(seconds=units)
        event = watcher.evaluate(state, obs, cfg, stamp)
        estimate = watcher.open_estimate(state, units, "v3", cfg.avg_request_price)
        say(f"request {units:2d}: est. {estimate:.3f} of {cfg.task_cap:.2f} credits")
        if event is not None:
            say(f"STOP: {event.message}")
            break
    if event is None:
        say("the recorded session stayed under the cap; nothing to stop")
        return 0

    from . import stop as stop_module
    stop_module.begin_stop(event, project=project, kiro_home=kiro_home,
                           announce_fn=lambda message, proj=None: None)

    say("")
    say("The agent now tries a tool call. The real pre-tool hook answers:")
    cli._PROJECT_OVERRIDE = project
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured):
            code = cli._hook_pre_tool(dict(SAMPLE_TOOL_CALL))
    finally:
        cli._PROJECT_OVERRIDE = None
    say(f"hook exit code: {code}")
    say(f"refusal text: {captured.getvalue().strip()}")

    autopsies = sorted((project / ".tombstone" / "autopsy").glob("*.md"))
    say("")
    if autopsies:
        say(f"receipt: {autopsies[-1]}")
    say(f"graveyard: tombstone graveyard --project '{project}'")
    say(f"The directory is kept so you can open the receipt: {project}")
    return 0
