"""Poll the active session and raise stall and over-cap events.

Task 6.1 covers the state file and project change detection:

- ``state.json`` is read and updated only through ``tombstones.update_json``
  (locked, atomic). Every path stored in it uses ``~`` for the home directory.
- The project fingerprint hashes ``(path, mtime_ns, size)`` for the files git
  reports as changed (``git status --porcelain=v1 -z``), or for a tree walk
  when git is not usable. ``.kiro/tombstone/``, ``.git/`` and
  ``tombstones.json`` never count as project changes (R5.4).
- Edits to git-ignored files are not seen when git is available. This is
  accepted and documented in design.md.

Task 6.2 adds ``evaluate`` (pure), ``poll_once`` and the loop ``run``.

The unit of the open-turn estimate (R4.1)
-----------------------------------------
While a turn is open the watcher counts ``assistant`` events of that turn.
That count overcounts real requests (session sess_b2bf8b22: 21 counted while
open, 12 requestIds at the end), so it is never priced with the per-request
average. The estimate is ``units x avg_unit_price`` where ``avg_unit_price``
is calibrated from the closed turns of this project as
``sum(actual) / sum(units)`` with the units counted the same way. See
design.md, section watcher.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from . import tombstones

STATE_VERSION = 1
STATE_NAME = "state.json"

# Directory names the tree walk fallback never enters.
WALK_SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "__pycache__"}
)
# Files that are Tombstone evidence, not project work.
IGNORED_FILES = frozenset({"tombstones.json"})
IGNORED_PREFIXES = (".kiro/tombstone/",)

GIT_TIMEOUT_SECONDS = 5


def default_state() -> dict:
    """A fresh ``state.json`` value with the fields from design.md."""
    return {
        "version": STATE_VERSION,
        "session": None,
        "hook_session_id": None,
        "turn": {},
        "current_task": None,
        "tasks_snapshot": {},
        "last_project_change": None,
        "project_fingerprint": None,
        "last_event": None,
        "avg_request_price": None,
        "price_window": [],
        "avg_unit_price": None,
        "unit_window": [],
        "unit_turns": [],
        "v3_project_hash": None,
        "cli_version": {"installed": None, "tested": None},
        "warnings": [],
    }


def state_path(project: Path) -> Path:
    """Path of ``state.json`` for a project."""
    return Path(project) / tombstones.TOMBSTONE_DIR / STATE_NAME


# --- home directory handling ---------------------------------------------


def to_tilde(value: str, home: Optional[Path] = None) -> str:
    """Replace a leading home directory in ``value`` with ``~``."""
    home_text = str(home if home is not None else Path.home())
    if not home_text or home_text == "/":
        return value
    # Replace the home directory wherever it appears as a whole path prefix,
    # so warning texts that embed a path are cleaned too.
    pattern = re.escape(home_text) + r"(?=/|\\|$|[^\w.\-])"
    return re.sub(pattern, "~", value)


def from_tilde(value: str, home: Optional[Path] = None) -> str:
    """Expand a leading ``~`` in ``value`` to the home directory."""
    home_text = str(home if home is not None else Path.home())
    if value == "~":
        return home_text
    if value.startswith("~" + os.sep):
        return home_text + value[1:]
    return value


def tilde_paths(obj: object, home: Optional[Path] = None) -> object:
    """Return a copy of ``obj`` with absolute home paths turned into ``~``.

    Strings that start with the home directory are rewritten; everything else
    is copied unchanged. Used on the whole state before it is stored.
    """
    if isinstance(obj, str):
        return to_tilde(obj, home)
    if isinstance(obj, dict):
        return {k: tilde_paths(v, home) for k, v in obj.items()}
    if isinstance(obj, list):
        return [tilde_paths(v, home) for v in obj]
    return obj


# --- state file ------------------------------------------------------------


def _with_defaults(value: Optional[dict]) -> dict:
    """Overlay a stored state on the defaults so every field exists."""
    state = default_state()
    if isinstance(value, dict):
        state.update(value)
    return state


def read_state(project: Path) -> dict:
    """Read ``state.json`` without writing. Missing or invalid gives defaults."""
    value, valid = tombstones._read_json(state_path(project))
    return _with_defaults(value if valid else None)


def update_state(
    project: Path,
    fn: Callable[[dict], Optional[dict]],
    home: Optional[Path] = None,
) -> dict:
    """Apply ``fn`` to the state under the file lock and store the result.

    ``fn`` may mutate the state in place and return None, or return a new
    dict. Missing fields are filled from the defaults first, and home paths
    are stored as ``~``. The file is written only through ``update_json``.
    """

    def apply(current: dict) -> dict:
        state = _with_defaults(current)
        result = fn(state)
        if result is not None:
            state = result
        return tilde_paths(state, home)  # type: ignore[return-value]

    return tombstones.update_json(state_path(project), apply, default_state())


def set_session(
    state: dict,
    session_id: str,
    engine: str,
    path: Path | str,
    home: Optional[Path] = None,
) -> dict:
    """Record the watched session in ``state`` with a ``~`` path."""
    state["session"] = {
        "id": session_id,
        "engine": engine,
        "path": to_tilde(str(path), home),
    }
    return state


# --- project change detection ---------------------------------------------


def is_ignored(rel_path: str) -> bool:
    """True for paths that are not project changes (R5.4)."""
    rel = rel_path.replace(os.sep, "/")
    while rel.startswith("./"):
        rel = rel[2:]
    if rel in IGNORED_FILES:
        return True
    if any(rel == p.rstrip("/") or rel.startswith(p) for p in IGNORED_PREFIXES):
        return True
    return rel == ".git" or rel.startswith(".git/") or "/.git/" in rel


def git_changed_files(project: Path) -> Optional[list[str]]:
    """Paths git reports as changed, or None when git cannot answer.

    Runs ``git status --porcelain=v1 -z --untracked-files=all``. A rename or
    copy entry is followed by its origin path, which is skipped.
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=str(project),
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None

    entries = proc.stdout.decode("utf-8", errors="replace").split("\0")
    paths: list[str] = []
    skip_next = False
    for entry in entries:
        if skip_next:
            skip_next = False
            continue
        if len(entry) < 4:
            continue
        status, name = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            skip_next = True
        paths.append(name)
    return paths


def walk_files(project: Path) -> list[str]:
    """Relative paths of project files for the no-git fallback."""
    project = Path(project)
    found: list[str] = []
    for root, dirs, files in os.walk(project):
        rel_root = os.path.relpath(root, project)
        rel_root = "" if rel_root == "." else rel_root.replace(os.sep, "/")
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in WALK_SKIP_DIRS
            and not is_ignored(f"{rel_root}/{d}".lstrip("/") + "/x")
        )
        for name in files:
            rel = f"{rel_root}/{name}".lstrip("/")
            if not is_ignored(rel):
                found.append(rel)
    return found


def _stat_token(project: Path, rel: str) -> str:
    """``mtime_ns:size`` of a file, or ``missing`` when it is gone."""
    try:
        info = os.stat(Path(project) / rel)
    except OSError:
        return "missing"
    return f"{info.st_mtime_ns}:{info.st_size}"


def project_fingerprint(project: Path) -> str:
    """Hash of path, mtime and size over the changed project files."""
    project = Path(project)
    listed = git_changed_files(project)
    if listed is None:
        listed = walk_files(project)
    rels = sorted({p for p in listed if not is_ignored(p)})
    digest = hashlib.sha256()
    for rel in rels:
        digest.update(f"{rel}\0{_stat_token(project, rel)}\n".encode("utf-8"))
    return digest.hexdigest()


def observe_change(state: dict, fingerprint: str, now: datetime) -> bool:
    """Compare ``fingerprint`` with the stored one and update ``state``.

    Returns True when the project changed since the previous poll. The first
    observation only stores the fingerprint: there is no earlier poll to
    differ from, so ``last_project_change`` stays as it was.
    """
    previous = state.get("project_fingerprint")
    state["project_fingerprint"] = fingerprint
    if previous is None or previous == fingerprint:
        return False
    state["last_project_change"] = now.isoformat()
    return True


def detect_change(project: Path, now: datetime, home: Optional[Path] = None) -> dict:
    """Fingerprint the project and store the result in ``state.json``.

    Returns the stored state. ``last_project_change`` moves to ``now`` when
    the fingerprint differs from the previous poll.
    """
    fingerprint = project_fingerprint(project)

    def apply(state: dict) -> None:
        observe_change(state, fingerprint, now)

    return update_state(project, apply, home)


# --- calibrated open-turn estimate (R4.1) ----------------------------------

POLL_SECONDS = 2.0
PULSE_SECONDS = 60
# Like the request price window: keep at least this many units of closed turns.
UNIT_WINDOW_UNITS = 50
UNIT_TURNS_KEPT = 200


def _round_credits(value: float) -> float:
    return round(float(value), 6)


def update_unit_window(state: dict, units: int, cost: float) -> dict:
    """Fold one closed, priced turn into the unit calibration of ``state``.

    ``units`` is the assistant event count of the turn, counted exactly as it
    is counted while the turn is open. ``unit_window`` is a list of
    ``[units, cost]``, newest last; oldest entries are dropped while the rest
    still total at least ``UNIT_WINDOW_UNITS``. ``avg_unit_price`` is the
    summed actual cost over the summed units. Returns the mutated state.
    """
    if not isinstance(units, int) or units <= 0 or not isinstance(cost, (int, float)):
        return state
    window = state.get("unit_window")
    if not isinstance(window, list):
        window = []
    window.append([units, _round_credits(cost)])
    total = sum(item[0] for item in window)
    while len(window) > 1 and total - window[0][0] >= UNIT_WINDOW_UNITS:
        total -= window[0][0]
        window.pop(0)
    sum_units = sum(item[0] for item in window)
    sum_cost = sum(item[1] for item in window)
    state["unit_window"] = window
    if sum_units > 0:
        state["avg_unit_price"] = round(sum_cost / sum_units, 6)
    return state


def unit_price(state: dict, engine: Optional[str], default_request_price: float
               ) -> tuple[float, str]:
    """Credits per counted unit of an open turn, and the basis of that figure.

    V3 with a calibration: ``avg_unit_price`` ("calibrated"). V3 without one
    yet: the per-request average, which overcounts and is labeled
    "uncalibrated". V2 counts real requests, so the per-request average is the
    right price ("per request").
    """
    request_price = tombstones._avg_price(state, default_request_price)
    if engine == "v2":
        return request_price, "per request"
    value = state.get("avg_unit_price")
    if isinstance(value, (int, float)) and value > 0:
        return float(value), "calibrated"
    return request_price, "uncalibrated"


def open_estimate(state: dict, units: int, engine: Optional[str],
                  default_request_price: float) -> float:
    """``units x avg credits per unit`` for the open turn (est.)."""
    price, _basis = unit_price(state, engine, default_request_price)
    return units * price


# --- evaluate (pure) -------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """What one poll saw. ``turn_id`` is None when no turn is open."""
    project: str = ""
    session_path: Optional[str] = None
    engine: Optional[str] = None
    session_id: Optional[str] = None
    turn_id: Optional[str] = None
    units: int = 0
    jsonl_size: int = 0
    prior_cost: float = 0.0     # earlier turns of the same task, actual or est.


@dataclass(frozen=True)
class Event:
    """A stall or over-cap event, handed to ``stop.begin_stop``."""
    kind: str                   # "stall" or "over_cap"
    at: str
    message: str
    detail: str
    project: str = ""
    task_id: Optional[str] = None
    spec: Optional[str] = None
    estimate: float = 0.0
    requests: int = 0
    session_id: Optional[str] = None
    turn_id: Optional[str] = None
    quiet_minutes: Optional[float] = None


def _dt(value: object) -> Optional[datetime]:
    """Parse a stored ISO time as an aware datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _fmt(value: float) -> str:
    return f"{value:.1f}" if value >= 10 else f"{value:.2f}"


def pulse_present(turn: dict, now: datetime) -> bool:
    """The session file grew at least once in the last 60 seconds."""
    grew = _dt(turn.get("last_growth_at"))
    return grew is not None and timedelta(0) <= now - grew <= timedelta(seconds=PULSE_SECONDS)


def quiet_since(state: dict, turn: dict) -> Optional[datetime]:
    """The later of the last project change and the turn start."""
    times = [t for t in (_dt(state.get("last_project_change")),
                         _dt(turn.get("started_at"))) if t is not None]
    return max(times) if times else None


def evaluate(state: dict, obs: Observation, cfg, now: datetime) -> Optional[Event]:
    """Decide whether this poll raises an event (R5.2, R5.3, R5.7).

    Pure: reads ``state`` (already updated for this poll), ``obs``, the config
    and ``now``; touches no disk, clock or process. Returns None when there is
    no open turn, when an event was already raised for this turn, or when
    nothing is wrong. Over-cap is checked before stall.

    - over-cap: ``est. > task cap``, with est. = open units x average credits
      per unit plus the cost of earlier turns of the same task.
    - stall: pulse present and quiet for longer than the quiet window. The
      quiet clock starts at the later of the last project change and the turn
      start, so changing files keeps moving it forward and no stall can come
      while files change (R5.7).
    """
    turn = state.get("turn")
    if obs.turn_id is None or not isinstance(turn, dict):
        return None
    if turn.get("event_raised") is True:
        return None

    task = state.get("current_task") if isinstance(state.get("current_task"), dict) else {}
    task_id = task.get("task_id")
    spec = task.get("spec")
    label = f"task {task_id}" if task_id else "this turn"

    estimate = obs.prior_cost + open_estimate(
        state, obs.units, obs.engine, cfg.avg_request_price)
    stamp = now.isoformat()

    def make(kind: str, message: str, detail: str,
             quiet: Optional[float] = None) -> Event:
        return Event(
            kind=kind, at=stamp, message=message, detail=detail,
            project=obs.project, task_id=task_id, spec=spec,
            estimate=round(estimate, 4), requests=obs.units,
            session_id=obs.session_id, turn_id=obs.turn_id, quiet_minutes=quiet,
        )

    if estimate > cfg.task_cap:
        return make(
            "over_cap",
            f"TOMBSTONE: {label} over budget (est. {_fmt(estimate)} of "
            f"{_fmt(cfg.task_cap)}). Stop.",
            f"est. {estimate:.3f} credits over task cap {cfg.task_cap}",
        )

    since = quiet_since(state, turn)
    if since is not None and pulse_present(turn, now):
        quiet = now - since
        if quiet > timedelta(minutes=cfg.quiet_window_minutes):
            minutes = quiet.total_seconds() / 60.0
            return make(
                "stall",
                f"TOMBSTONE: {label} stalled, no project change for "
                f"{minutes:.0f} min while the agent keeps calling the model "
                f"(est. {_fmt(estimate)}). Stop.",
                f"quiet {minutes:.1f} min, window {cfg.quiet_window_minutes} min",
                quiet=round(minutes, 2),
            )
    return None


# --- poll_once -------------------------------------------------------------


def _prior_task_cost(records: list, state: dict, open_turn_id: Optional[str]) -> float:
    """Cost of earlier recorded turns of the current task (actual, else est.)."""
    from . import caps

    task = state.get("current_task")
    if not isinstance(task, dict) or not task.get("task_id"):
        return 0.0
    total = 0.0
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("task_id") != task.get("task_id") \
                or record.get("spec") != task.get("spec"):
            continue
        if record.get("outcome") == "cap":
            continue
        turns = record.get("turns")
        if open_turn_id and isinstance(turns, list) \
                and any(isinstance(t, dict) and t.get("turn_id") == open_turn_id
                        for t in turns):
            continue
        value, _basis = caps._record_cost(record)
        if value is not None:
            total += value
    return total


def _open_turn(session):
    """The newest open turn of a parsed session, or None."""
    for turn in reversed(session.turns):
        if turn.state == "open":
            return turn
    return None


def poll_once(project: Path, cfg, now: datetime,
              kiro_home: Optional[Path] = None) -> Optional[Event]:
    """One poll: refresh ``state.json`` and return an event or None (R5.1).

    Reads the session and the project outside the lock, then updates the state
    in one locked write: session, project change, unit calibration from newly
    closed turns, the open-turn tracking, the estimate, and the event once per
    turn. The estimate is open-turn units x average credits per unit.
    """
    from . import sessions

    project = Path(project)
    before = read_state(project)
    fingerprint = project_fingerprint(project)

    # The id a hook saw on STDIN wins; the index lookup is the fallback.
    hook_id = before.get("hook_session_id")
    session_path = sessions.find_session(
        project, hook_id if isinstance(hook_id, str) and hook_id else None,
        kiro_home=kiro_home,
        v3_project_hash=before.get("v3_project_hash"),
    )
    session = None
    progress = None
    if session_path is not None:
        session = sessions.parse_session(session_path)
        progress = sessions.open_turn_progress(session_path)

    # Calibration inputs: closed, priced V3 turns not folded yet.
    fresh: list[tuple[str, int, float]] = []
    if session is not None and session.engine == "v3":
        known = set(before.get("unit_turns") or [])
        wanted = [t for t in session.turns
                  if t.state == "closed" and isinstance(t.cost, (int, float))
                  and t.turn_id not in known]
        if wanted:
            counts = sessions.assistant_counts(session_path)
            for t in wanted:
                units = counts.get(t.turn_id, 0)
                if units > 0:
                    fresh.append((t.turn_id, units, float(t.cost)))

    open_turn = _open_turn(session) if session is not None else None
    if open_turn is None and session is not None and session.engine == "v2" \
            and progress is not None and progress.requests_so_far > 0:
        open_turn = type("OpenV2", (), {
            "turn_id": f"open-{len(session.turns)}", "start": None,
            "requests": progress.requests_so_far})()

    records: list = []
    if open_turn is not None:
        store = tombstones.load_tombstones(project / "tombstones.json")
        records = store.get("tombstones", []) if isinstance(store, dict) else []

    raised: list[Event] = []

    def apply(state: dict) -> None:
        if session is not None and session_path is not None:
            set_session(state, session.session_id, session.engine or "unknown",
                        session_path)
            cached = sessions.v3_project_hash(session_path)
            if cached:
                state["v3_project_hash"] = cached
        observe_change(state, fingerprint, now)

        seen = state.get("unit_turns")
        seen = seen if isinstance(seen, list) else []
        for turn_id, units, cost in fresh:
            if turn_id in seen:
                continue
            update_unit_window(state, units, cost)
            seen.append(turn_id)
        state["unit_turns"] = seen[-UNIT_TURNS_KEPT:]

        turn = state.get("turn")
        if not isinstance(turn, dict):
            turn = {}
            state["turn"] = turn

        if open_turn is None:
            turn["watch_turn_id"] = None
            return

        units = open_turn.requests if isinstance(open_turn.requests, int) else 0
        if progress is not None:
            units = max(units, progress.requests_so_far) \
                if session.engine == "v2" else units
        size = progress.size_bytes if progress is not None else 0
        if turn.get("watch_turn_id") != open_turn.turn_id:
            started = open_turn.start or now
            turn["watch_turn_id"] = open_turn.turn_id
            turn["started_at"] = started.isoformat()
            turn["jsonl_size"] = size
            turn["last_growth_at"] = started.isoformat()
            turn["event_raised"] = False
        else:
            if size > (turn.get("jsonl_size") or 0):
                turn["last_growth_at"] = now.isoformat()
            turn["jsonl_size"] = size

        engine = session.engine
        obs = Observation(
            project=str(project), session_path=str(session_path), engine=engine,
            session_id=session.session_id, turn_id=open_turn.turn_id,
            units=units, jsonl_size=size,
            prior_cost=_prior_task_cost(records, state, open_turn.turn_id),
        )
        price, basis = unit_price(state, engine, cfg.avg_request_price)
        turn["requests"] = units
        turn["estimate"] = round(obs.prior_cost + units * price, 4)
        turn["estimate_basis"] = f"est., {basis}"

        event = evaluate(state, obs, cfg, now)
        if event is not None:
            turn["event_raised"] = True
            state["last_event"] = {"kind": event.kind, "at": event.at,
                                   "detail": event.detail}
            raised.append(event)

    update_state(project, apply)
    return raised[0] if raised else None


# --- the loop --------------------------------------------------------------


def _log(project: Path, message: str) -> None:
    sidecar = tombstones._sidecar_dir(state_path(project))
    tombstones._log_warning(sidecar, message)


def dispatch_event(project: Path, event: Event) -> None:
    """Hand an event to ``stop.begin_stop``; log when it cannot be done.

    ``begin_stop`` lives in ``stop.py`` (task 7.4). If it is ever missing the
    event is only logged, so the loop stays alive and nothing is lost silently.
    """
    from . import stop

    begin = getattr(stop, "begin_stop", None)
    if begin is None:
        _log(project, f"event {event.kind} raised, stop.begin_stop not available: "
                      f"{event.message}")
        return
    begin(event)


def kill_step_once(project: Path, cfg, now: datetime) -> Optional[dict]:
    """Step two of a stop, checked on every poll (R6.3).

    Does nothing unless ``state.stop`` exists. The request count is the unit
    count of the open turn, the same scale as ``requests_at_stop``. With no
    open turn the count is unknown and ``maybe_kill`` does not act.
    """
    from . import stop

    state = read_state(project)
    if not isinstance(state.get("stop"), dict):
        return None
    turn = state.get("turn") if isinstance(state.get("turn"), dict) else {}
    requests_now = turn.get("requests") if turn.get("watch_turn_id") else None
    result = stop.maybe_kill(
        project, requests_now=requests_now, now=now,
        grace_seconds=cfg.kill_grace_seconds,
    )
    if result.get("status") in ("killed", "no_process"):
        _log(project, f"stop step two: {result.get('status')}"
                      f" {result.get('warning') or ''}".rstrip())
    return result


# --- CLI version check (R10.4) ----------------------------------------------

# The Kiro CLI version this release was measured against. Hooks never probe it.
TESTED_CLI_VERSION = "3.1.0"
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.-]+)?")


def installed_cli_version() -> Optional[str]:
    """Version printed by ``kiro-cli --version``, or None when unavailable."""
    import subprocess

    try:
        done = subprocess.run(["kiro-cli", "--version"], capture_output=True,
                              text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    match = _VERSION_RE.search(done.stdout or "")
    return match.group(0) if match else None


def check_cli_version(project: Path, installed: Optional[str] = None,
                      probe: Optional[Callable[[], Optional[str]]] = None
                      ) -> Optional[str]:
    """Store installed and tested versions in ``state.cli_version``.

    Returns one warning line when the installed version is known and differs
    from ``TESTED_CLI_VERSION``, else None. Never raises: the check must not
    break ``init`` or the watcher.
    """
    try:
        if installed is None:
            installed = (probe or installed_cli_version)()

        def apply(state: dict) -> None:
            state["cli_version"] = {"installed": installed,
                                    "tested": TESTED_CLI_VERSION}

        update_state(Path(project), apply)
    except Exception:  # noqa: BLE001
        return None
    if installed and installed != TESTED_CLI_VERSION:
        return (f"warning: kiro-cli {installed} is installed, tombstone was "
                f"tested on {TESTED_CLI_VERSION}; numbers may be off")
    return None


def run(project: Path, *, kiro_home: Optional[Path] = None,
        interval: float = POLL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Optional[Callable[[], datetime]] = None,
        max_polls: Optional[int] = None,
        dispatch: Optional[Callable[[Path, Event], None]] = None,
        kill_step: Optional[Callable[..., object]] = None) -> int:
    """Poll every ``interval`` seconds until stopped (R5.1).

    A failure in one poll is logged to ``errors.log`` and the loop goes on.
    Config is reloaded each poll so a raised cap takes effect at once.
    ``max_polls``, ``sleep``, ``clock`` and ``dispatch`` exist for tests.
    Returns the number of polls made.
    """
    from . import autopsy, caps

    project = Path(project)
    clock = clock or (lambda: datetime.now(timezone.utc))
    dispatch = dispatch or dispatch_event
    kill_step = kill_step or kill_step_once
    polls = 0
    while max_polls is None or polls < max_polls:
        polls += 1
        try:
            now = clock()
            cfg = caps.load_config(project)
            event = poll_once(project, cfg, now, kiro_home)
            if event is not None:
                dispatch(project, event)
            kill_step(project, cfg, now)
            tombstones.reconcile_pending(project, kiro_home=kiro_home)
            autopsy.settle_autopsy(project, kiro_home=kiro_home)
        except Exception as exc:  # the loop must stay alive
            try:
                _log(project, f"watcher poll failed: {type(exc).__name__}: {exc}")
            except Exception:
                pass
        if max_polls is None or polls < max_polls:
            sleep(interval)
    return polls
