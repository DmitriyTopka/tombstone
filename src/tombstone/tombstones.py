"""Project-local data: records, the locked JSON store, tasks.md, graveyard.

Task 3.1 implements the locked JSON store shared by ``tombstones.json``,
``state.json`` and the usage cache, plus load and upsert helpers for
``tombstones.json``. Later tasks add tasks.md parsing, record building and
the graveyard renderer.

The store takes an exclusive ``fcntl.flock`` on a lock file, reads the current
value, applies a caller function, writes a temp file in the same directory,
``fsync`` it, then ``os.replace`` so a reader never sees a partial file
(R2.5). When the existing file is not valid JSON the store leaves it
untouched, queues the change in ``pending.jsonl`` and logs a warning. No user
file is ever overwritten with a default or deleted. The next successful write
merges the queued pending changes first.
"""

from __future__ import annotations

import fcntl
import functools
import html
import json
import math
import os
import re
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .sidecar import SIDECAR, migrate as migrate_sidecar
from .safepath import (
    append_nofollow,
    guard,
    is_safe_target,
    open_nofollow,
    write_atomic,
)

# Directory that holds the lock files, pending queues and the error log.
TOMBSTONE_DIR = SIDECAR

TOMBSTONES_VERSION = 1


def _tombstone_dir(path: Path) -> Path:
    """Return the ``.tombstone`` directory for a target file.

    ``tombstones.json`` lives at the repo root, while ``state.json`` and the
    usage cache live in ``.tombstone``. The lock, pending and log files
    all live in ``.tombstone`` next to the state so they stay out of the
    committed data. We walk up from the target until we find a ``.tombstone``
    directory (moving an old ``.kiro/tombstone`` there first, R12.2),
    falling back to the target's own parent.
    """
    path = Path(path)
    if path.parent.name == SIDECAR.name:
        migrate_sidecar(path.parent.parent)
        return path.parent
    for parent in path.parents:
        candidate = parent / TOMBSTONE_DIR
        if not candidate.is_dir():
            migrate_sidecar(parent)
        if candidate.is_dir():
            return candidate
    # No project marker yet: place sidecar files beside the target.
    return path.parent


def _project_of_sidecar(sidecar: Path) -> Path:
    """The project root for a sidecar directory (its parent when it is
    ``.tombstone``, else the directory itself)."""
    sidecar = Path(sidecar)
    if sidecar.name == SIDECAR.name:
        return sidecar.parent
    return sidecar


def _sidecar_dir(path: Path) -> Path:
    """Create and return the directory for lock, pending and log files."""
    directory = _tombstone_dir(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _log_warning(sidecar: Path, message: str) -> None:
    """Append one warning line to ``errors.log``. Never raises."""
    stamp = datetime.now(timezone.utc).isoformat()
    try:
        if not guard(_project_of_sidecar(sidecar), sidecar / "errors.log"):
            return
        append_nofollow(sidecar / "errors.log", f"{stamp} {message}\n")
    except OSError:
        pass


def _read_json(path: Path) -> tuple[Optional[dict], bool]:
    """Read a JSON object from ``path``.

    Returns ``(value, valid)``. ``valid`` is False when the file exists but is
    not a JSON object. A missing file is reported as ``(None, True)`` so the
    caller may apply the default without treating it as corruption.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, True
    except OSError:
        return None, False
    if not text.strip():
        # An empty file is treated as missing, not as corruption.
        return None, True
    try:
        value = json.loads(text)
    except ValueError:
        return None, False
    if not isinstance(value, dict):
        return None, False
    return value, True


def _atomic_write(path: Path, value: dict) -> None:
    """Write ``value`` as pretty JSON atomically (temp file, fsync, replace).

    Goes through ``safepath.write_atomic``, which refuses a symlink target.
    """
    write_atomic(path, json.dumps(value, indent=2, sort_keys=False) + "\n")


def _queue_pending(sidecar: Path, name: str, change: object) -> None:
    """Append a change to the per-target ``pending.jsonl`` queue. Never raises."""
    line = json.dumps(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "target": name,
            "change": change,
        }
    )
    try:
        queue = sidecar / "pending.jsonl"
        if not guard(_project_of_sidecar(sidecar), queue):
            return
        append_nofollow(queue, line + "\n")
    except (OSError, TypeError, ValueError):
        pass


def _drain_pending(sidecar: Path, name: str) -> list[dict]:
    """Read and clear the pending changes queued for ``name``.

    Returns the list of queued ``change`` objects in order. Lines for other
    targets are preserved. A missing or unreadable queue yields an empty list.
    """
    queue = sidecar / "pending.jsonl"
    if not guard(_project_of_sidecar(sidecar), queue):
        return []
    try:
        with open_nofollow(queue, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return []
    drained: list[dict] = []
    kept: list[str] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("target") == name:
            change = obj.get("change")
            if isinstance(change, dict):
                drained.append(change)
        else:
            kept.append(raw)
    if drained:
        try:
            if kept:
                write_atomic(queue, "\n".join(kept) + "\n")
            else:
                queue.unlink()
        except OSError:
            pass
    return drained


def update_json(
    path: Path,
    fn: Callable[[dict], dict],
    default: dict,
    pending_apply: Optional[Callable[[dict, dict], dict]] = None,
) -> dict:
    """Read, transform and atomically write a JSON object under a file lock.

    Takes an exclusive ``fcntl.flock`` on ``<name>.lock`` inside
    ``.tombstone``, reads the current value (or ``default`` when the file
    is absent), applies ``fn``, writes a temp file in the same directory,
    ``fsync`` it, then ``os.replace`` so readers never see a partial file
    (R2.5).

    When the existing file is not valid JSON the file is left untouched, the
    requested change is queued in ``pending.jsonl`` and a warning is logged
    (R2.5): no user file is ever overwritten with a default or deleted. The
    returned value in that case is the result of ``fn(default)`` so callers
    still see what they intended to write.

    When ``pending_apply`` is given, any changes queued for this target are
    folded into the value with it before ``fn`` runs, so a recovered write
    includes the changes that were deferred while the file was broken.
    """
    path = Path(path)
    sidecar = _tombstone_dir(path)
    project = (sidecar.parent if sidecar.name == SIDECAR.name
               else path.parent)
    lock_path = sidecar / f"{path.name}.lock"
    if not (guard(project, sidecar) and guard(project, path)
            and guard(project, lock_path)):
        # Symlinked sidecar or state path: write nothing, report the intent.
        return fn(json.loads(json.dumps(default)))
    sidecar = _sidecar_dir(path)
    try:
        # Mode "a" creates the lock file without truncating it and refuses a
        # symlink in the last component.
        lock_handle = open_nofollow(lock_path, "a")
    except OSError:
        return fn(json.loads(json.dumps(default)))

    with lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            current, valid = _read_json(path)

            if not valid:
                # The file on disk is corrupt. Do not touch it. Queue the
                # intended change and return what the caller meant to write.
                intended = fn(json.loads(json.dumps(default)))
                _queue_pending(sidecar, path.name, intended)
                _log_warning(
                    sidecar,
                    f"{path.name} is not valid JSON, change queued in pending.jsonl",
                )
                return intended

            value = current if current is not None else json.loads(json.dumps(default))

            # Fold any previously queued changes into the value first.
            drained = _drain_pending(sidecar, path.name)
            if drained and pending_apply is not None:
                for change in drained:
                    value = pending_apply(value, change)

            value = fn(value)
            _atomic_write(path, value)
            return value
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


# --- tombstones.json helpers -----------------------------------------------


def _empty_store() -> dict:
    """A fresh, valid ``tombstones.json`` value."""
    return {"version": TOMBSTONES_VERSION, "tombstones": []}


def load_tombstones(path: Path) -> dict:
    """Load ``tombstones.json`` as a dict, tolerating absence or corruption.

    A missing or unreadable or invalid file yields a fresh empty store. The
    file on disk is never modified by a read. The returned value always has a
    ``version`` and a list under ``tombstones``.
    """
    value, valid = _read_json(Path(path))
    if not valid or value is None:
        return _empty_store()
    records = value.get("tombstones")
    if not isinstance(records, list):
        value["tombstones"] = []
    value.setdefault("version", TOMBSTONES_VERSION)
    return value


def _merge_record(existing: dict, incoming: dict) -> dict:
    """Merge one incoming record into an existing one by turn id.

    Top-level fields from ``incoming`` win. Turn entries are upserted by
    ``turn_id`` so re-running the same hook stays idempotent (R2.1).
    """
    merged = dict(existing)
    for key, value in incoming.items():
        if key == "turns":
            continue
        merged[key] = value

    turns: list[dict] = list(existing.get("turns") or [])
    index = {
        turn.get("turn_id"): pos
        for pos, turn in enumerate(turns)
        if isinstance(turn, dict)
    }
    for turn in incoming.get("turns") or []:
        if not isinstance(turn, dict):
            continue
        turn_id = turn.get("turn_id")
        if turn_id in index:
            turns[index[turn_id]] = turn
        else:
            index[turn_id] = len(turns)
            turns.append(turn)
    merged["turns"] = turns
    return merged


def upsert_record(store: dict, record: dict) -> dict:
    """Insert or update one record in a loaded store, keyed by ``id``.

    Returns the same ``store`` dict for chaining. A record with a matching
    ``id`` is merged (its turns upserted by ``turn_id``); otherwise the record
    is appended. This is the function handed to ``update_json`` by the Stop
    hook in task 3.3.
    """
    records = store.get("tombstones")
    if not isinstance(records, list):
        records = []
        store["tombstones"] = records
    store.setdefault("version", TOMBSTONES_VERSION)

    record_id = record.get("id")
    for pos, existing in enumerate(records):
        if isinstance(existing, dict) and existing.get("id") == record_id:
            records[pos] = _merge_record(existing, record)
            return store
    records.append(record)
    return store


def apply_pending_record(store: dict, change: dict) -> dict:
    """Fold a queued change back into the store (for ``update_json``).

    A queued change for ``tombstones.json`` is the whole intended store value
    (the result of the deferred ``fn``), so we upsert each of its records into
    the current store. A bare record (one with an ``id`` and no ``tombstones``
    list) is upserted directly.
    """
    if isinstance(change.get("tombstones"), list):
        for record in change["tombstones"]:
            if isinstance(record, dict):
                upsert_record(store, record)
        return store
    if "id" in change:
        return upsert_record(store, change)
    return store


# --- tasks.md parsing and task id resolution (task 3.2) --------------------


@dataclass(frozen=True)
class Task:
    """One task line in a ``tasks.md`` file.

    ``task_id`` is the dotted number ("11", "4.2"). A line that has a checkbox
    but no number gets the id ``L<line>`` so it can still be referenced.
    ``checked`` is True for ``[x]`` or ``[X]`` and False for ``[ ]`` or
    ``[-]``. ``line`` is 1-based.
    """
    spec: str
    task_id: str
    name: str
    checked: bool
    line: int


@dataclass(frozen=True)
class TaskRef:
    """The task a turn is attributed to, and how it was resolved.

    ``task_id`` is ``unknown`` only when every resolution step failed, in
    which case ``turn_id`` carries the turn so the record id stays unique
    (R2.4). ``source`` names the step that produced the ref for diagnostics.
    """
    spec: Optional[str]
    task_id: str
    name: Optional[str]
    source: str
    turn_id: Optional[str] = None


# A task line: optional indent, "- [", a status char, "]", an optional "*",
# then the dotted number (optionally followed by a dot) and the name.
_TASK_LINE = re.compile(r"^\s*- \[([ xX\-])\]\*?\s+(\d+(?:\.\d+)*)\.?\s+(.+?)\s*$")
# A checkbox line that has no number (feature-level bullets, prose checklists).
_CHECK_LINE = re.compile(r"^\s*- \[([ xX\-])\]\*?\s+(.+?)\s*$")


def _spec_of(path: Path) -> str:
    """Return the ``<spec>`` folder name for a ``.kiro/specs/<spec>/tasks.md``.

    Falls back to the parent directory name when the path does not sit under
    a ``specs`` directory, so a stray ``tasks.md`` still parses.
    """
    path = Path(path)
    parent = path.parent
    if parent.parent.name == "specs":
        return parent.name
    return parent.name


def parse_tasks(path: Path) -> list[Task]:
    """Parse a ``tasks.md`` into a list of ``Task``. Never raises.

    Each line matching the task pattern becomes a ``Task``. A checkbox line
    without a number is kept with the id ``L<line>`` (R2.3). A missing or
    unreadable file yields an empty list (R2.4).
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    spec = _spec_of(path)
    tasks: list[Task] = []
    for index, raw in enumerate(text.splitlines(), start=1):
        match = _TASK_LINE.match(raw)
        if match is not None:
            status, task_id, name = match.group(1), match.group(2), match.group(3)
            tasks.append(
                Task(
                    spec=spec,
                    task_id=task_id,
                    name=name.strip(),
                    checked=status in ("x", "X"),
                    line=index,
                )
            )
            continue
        check = _CHECK_LINE.match(raw)
        if check is not None:
            status, name = check.group(1), check.group(2)
            tasks.append(
                Task(
                    spec=spec,
                    task_id=f"L{index}",
                    name=name.strip(),
                    checked=status in ("x", "X"),
                    line=index,
                )
            )
    return tasks


def _specs_dir(project: Path) -> Path:
    return Path(project) / ".kiro" / "specs"


def _tasks_file(project: Path, spec: str) -> Path:
    return _specs_dir(project) / spec / "tasks.md"


def _list_specs(project: Path) -> list[str]:
    """Spec folder names that have a ``tasks.md``. Sorted, never raises."""
    specs_dir = _specs_dir(project)
    names: list[str] = []
    try:
        for child in specs_dir.iterdir():
            if child.is_dir() and (child / "tasks.md").is_file():
                names.append(child.name)
    except OSError:
        return []
    return sorted(names)


def active_spec(project: Path, state: dict) -> Optional[str]:
    """Pick the active spec for the project (design: active spec order).

    In order:
    1. The spec of the last ``tasks.md`` the agent touched, recorded by the
       post-tool hook in ``state.last_tasks_spec``.
    2. The spec whose ``tasks.md`` was modified most recently and still has an
       unchecked task.
    3. The only spec, when exactly one exists.
    Returns None when no spec can be chosen.
    """
    project = Path(project)

    last = state.get("last_tasks_spec") if isinstance(state, dict) else None
    if isinstance(last, str) and _tasks_file(project, last).is_file():
        return last

    specs = _list_specs(project)
    if not specs:
        return None

    with_unchecked: list[tuple[float, str]] = []
    for spec in specs:
        path = _tasks_file(project, spec)
        tasks = parse_tasks(path)
        if any(not task.checked for task in tasks):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            with_unchecked.append((mtime, spec))
    if with_unchecked:
        with_unchecked.sort(key=lambda item: item[0], reverse=True)
        return with_unchecked[0][1]

    if len(specs) == 1:
        return specs[0]
    return None


def _stdin_task_id(stdin: dict) -> Optional[str]:
    """Pull a task id from hook STDIN, tolerating several shapes (R2.3).

    Looks for ``task_id``, ``taskId`` and a nested ``task.id``. The exact
    STDIN of the V3 ``PreTaskExecution`` event is not pinned down, so the
    lookup is deliberately forgiving. Returns None when nothing is found.
    """
    if not isinstance(stdin, dict):
        return None
    for key in ("task_id", "taskId"):
        value = stdin.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    task = stdin.get("task")
    if isinstance(task, dict):
        value = task.get("id")
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return None


def stdin_session_id(stdin: dict) -> Optional[str]:
    """The ``session_id`` a hook received on STDIN, or None."""
    if not isinstance(stdin, dict):
        return None
    for key in ("session_id", "sessionId"):
        value = stdin.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def remember_session(project: Path, stdin: dict) -> None:
    """Store the STDIN session id in ``state.hook_session_id`` (watcher input).

    Reads first and writes only when the id changed, so a hook that sees the
    same session again costs one small read and no lock.
    """
    session_id = stdin_session_id(stdin)
    if session_id is None:
        return
    state_path = Path(project) / TOMBSTONE_DIR / "state.json"
    current, valid = _read_json(state_path)
    if valid and isinstance(current, dict) \
            and current.get("hook_session_id") == session_id:
        return

    def mark(state: dict) -> dict:
        state["hook_session_id"] = session_id
        return state

    update_json(state_path, mark, default={"version": 1})


def stdin_turn_id(stdin: dict) -> Optional[str]:
    """Pull the current turn id from hook STDIN, tolerating several shapes.

    Looks for ``turn_id``, ``turnId`` and a nested ``turn.id`` / ``turn.turnId``.
    The exact STDIN of a ``PostToolUse`` or ``UserPromptSubmit`` event is not
    pinned down, so the lookup is deliberately forgiving. Returns None when
    nothing is found.
    """
    if not isinstance(stdin, dict):
        return None
    for key in ("turn_id", "turnId"):
        value = stdin.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    turn = stdin.get("turn")
    if isinstance(turn, dict):
        for key in ("id", "turn_id", "turnId"):
            value = turn.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return None


def _name_for_task(project: Path, spec: Optional[str], task_id: str) -> Optional[str]:
    """Look up a task name by id in the spec's ``tasks.md``, or None."""
    if not spec:
        return None
    for task in parse_tasks(_tasks_file(project, spec)):
        if task.task_id == task_id:
            return task.name
    return None


def _checked_this_turn(state: dict) -> list[str]:
    """Task ids the post-tool hook recorded as newly checked this turn."""
    turn = state.get("turn") if isinstance(state, dict) else None
    if isinstance(turn, dict):
        checked = turn.get("checked")
        if isinstance(checked, list):
            return [str(item) for item in checked if isinstance(item, (str, int))]
    return []


def _task_sort_key(task_id: str) -> tuple:
    """Natural order for dotted ids: 2.10 after 2.9, non-numeric last."""
    parts = []
    for part in str(task_id).split("."):
        parts.append((0, int(part), "") if part.isdigit() else (1, 0, part))
    return tuple(parts)


def _prompt_name(state: dict) -> Optional[str]:
    """The prompt name stored by the prompt hook for this turn, or None."""
    turn = state.get("turn") if isinstance(state, dict) else None
    if isinstance(turn, dict):
        name = turn.get("prompt_name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def resolve_tasks(project: Path, stdin: dict, state: dict) -> list[TaskRef]:
    """Resolve every task a turn belongs to, first match wins (R2.3, R2.4).

    1. A task id carried in the hook STDIN: one ref.
    2. The boxes checked during the turn (``state.turn.checked``): one ref per
       id, in natural order. The turn cost is split equally between them.
    3. ``unknown``: one ref named after the prompt (``state.turn.prompt_name``)
       when known. The turn is a work turn outside the task list. There is no
       "first unchecked task" guess.
    """
    project = Path(project)
    spec = active_spec(project, state)

    stdin_id = _stdin_task_id(stdin)
    if stdin_id is not None:
        return [TaskRef(
            spec=spec,
            task_id=stdin_id,
            name=_name_for_task(project, spec, stdin_id),
            source="stdin",
        )]

    checked = sorted(set(_checked_this_turn(state)), key=_task_sort_key)
    if checked:
        return [
            TaskRef(
                spec=spec,
                task_id=task_id,
                name=_name_for_task(project, spec, task_id),
                source="checkbox-diff",
            )
            for task_id in checked
        ]

    turn = state.get("turn") if isinstance(state, dict) else None
    turn_id = turn.get("turn_id") if isinstance(turn, dict) else None
    return [TaskRef(
        spec=spec,
        task_id="unknown",
        name=_prompt_name(state),
        source="prompt" if _prompt_name(state) else "fallback",
        turn_id=str(turn_id) if turn_id else None,
    )]


def resolve_task(project: Path, stdin: dict, state: dict) -> TaskRef:
    """Resolve the first task of a turn: see :func:`resolve_tasks`."""
    return resolve_tasks(project, stdin, state)[0]


# --- prompt hook: name the turn, reset the checked list (R2.3, R2.4) -------

PROMPT_NAME_WORDS = 8

_HOME_PATH = re.compile(r"(?:/Users|/home)/[^/\s]+")


def prompt_name(prompt: object, home: Optional[Path] = None) -> Optional[str]:
    """First 8 words of a prompt with home paths stripped, or None.

    The home directory of the current user and any ``/Users/<name>`` or
    ``/home/<name>`` prefix become ``~``. Never raises.
    """
    if not isinstance(prompt, str):
        return None
    text = prompt
    try:
        home_text = str(home if home is not None else Path.home())
    except (RuntimeError, OSError):
        home_text = ""
    if len(home_text) > 1:
        text = text.replace(home_text, "~")
    text = _HOME_PATH.sub("~", text)
    words = text.split()[:PROMPT_NAME_WORDS]
    return " ".join(words) if words else None


def record_prompt(state: dict, stdin: dict, home: Optional[Path] = None) -> dict:
    """Store the prompt name for the new turn and reset ``turn.checked``.

    Called by ``tombstone hook prompt`` through the locked store. Every prompt
    starts a new turn, so the checked list is always cleared. Returns the
    mutated state.
    """
    turn = state.get("turn")
    if not isinstance(turn, dict):
        turn = {}
        state["turn"] = turn
    turn["checked"] = []
    turn["prompt_name"] = prompt_name(stdin.get("prompt"), home)
    turn_id = stdin_turn_id(stdin)
    if turn_id is not None:
        turn["turn_id"] = turn_id
    return state


# --- post-tool hook: refresh the snapshot, record newly checked ids --------


# A tool input path points at a spec's tasks.md: ``.kiro/specs/<spec>/tasks.md``.
_TASKS_PATH = re.compile(r"[\\/]\.kiro[\\/]specs[\\/]([^\\/]+)[\\/]tasks\.md$")


def tasks_path_spec(text: object) -> Optional[str]:
    """Return the ``<spec>`` if ``text`` is a spec ``tasks.md`` path, else None.

    A cheap string check so the post-tool hook does nothing for other tools
    (design: the hook runs only when the tool input path matches
    ``.kiro/specs/<spec>/tasks.md``).
    """
    if not isinstance(text, str) or not text:
        return None
    normalized = text.replace("\\", "/")
    match = _TASKS_PATH.search(normalized)
    if match is not None:
        return match.group(1)
    return None


def _collect_paths(value: object, out: list[str]) -> None:
    """Gather every string value from a nested STDIN structure."""
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_paths(item, out)
    elif isinstance(value, list):
        for item in value:
            _collect_paths(item, out)


def stdin_tasks_spec(stdin: dict) -> Optional[str]:
    """Find a spec whose ``tasks.md`` appears anywhere in the hook STDIN.

    The tool input shape varies per tool, so every string in the STDIN is
    checked against the ``.kiro/specs/<spec>/tasks.md`` pattern. Returns the
    first matching spec, or None.
    """
    paths: list[str] = []
    _collect_paths(stdin, paths)
    for text in paths:
        spec = tasks_path_spec(text)
        if spec is not None:
            return spec
    return None


def _snapshot_for(project: Path, spec: str) -> dict:
    """The ``{task_id: checked}`` snapshot of a spec's tasks.md right now."""
    return {
        task.task_id: task.checked
        for task in parse_tasks(_tasks_file(project, spec))
    }


def _checkbox_ids(text: object) -> dict[str, bool]:
    """Map ``{task_id: checked}`` for every task line in a blob of text.

    Reuses the same two patterns ``parse_tasks`` uses so a line understood by
    the parser is understood here. A numberless checkbox line is keyed by
    ``L<line>`` just like the parser. Non-string input yields an empty map.
    """
    result: dict[str, bool] = {}
    if not isinstance(text, str) or not text:
        return result
    for index, raw in enumerate(text.splitlines(), start=1):
        match = _TASK_LINE.match(raw)
        if match is not None:
            status, task_id = match.group(1), match.group(2)
            result[task_id] = status in ("x", "X")
            continue
        check = _CHECK_LINE.match(raw)
        if check is not None:
            status = check.group(1)
            result[f"L{index}"] = status in ("x", "X")
    return result


def _edit_strings(stdin: dict) -> list[tuple[Optional[str], Optional[str]]]:
    """Collect ``(oldStr, newStr)`` pairs from a hook STDIN edit payload.

    The exact STDIN shape of a ``PostToolUse`` event is not pinned down, so the
    search is tolerant: it walks the whole structure and, at any dict that
    carries an ``oldStr``/``old_str`` and/or ``newStr``/``new_str`` key, records
    that pair. A ``str_replace`` edit yields one pair with both sides; an
    ``fs_write`` yields nothing (no old side to diff against). Never raises.
    """
    pairs: list[tuple[Optional[str], Optional[str]]] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            old = None
            new = None
            has = False
            for old_key in ("oldStr", "old_str"):
                if old_key in node and isinstance(node[old_key], str):
                    old = node[old_key]
                    has = True
            for new_key in ("newStr", "new_str"):
                if new_key in node and isinstance(node[new_key], str):
                    new = node[new_key]
                    has = True
            if has:
                pairs.append((old, new))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(stdin)
    return pairs


def _newly_checked_from_edit(stdin: dict) -> list[str]:
    """Task ids a single edit flipped from ``[ ]`` to ``[x]`` (R2.3, Bug 2).

    For a ``str_replace`` edit, a task id whose checkbox is unchecked in
    ``oldStr`` and checked in ``newStr`` is the task completed by this edit.
    For ``fs_write`` there is no ``oldStr`` to compare against, so this returns
    nothing and the caller treats no task as newly checked. Never raises.
    """
    found: list[str] = []
    for old, new in _edit_strings(stdin):
        old_map = _checkbox_ids(old)
        new_map = _checkbox_ids(new)
        for task_id, checked in new_map.items():
            # Only a line present as unchecked in oldStr can flip; a line that
            # is new in the edit is never "checked now".
            if checked and old_map.get(task_id) is False:
                if task_id not in found:
                    found.append(task_id)
    return found


def _reset_turn_if_new(state: dict, turn_id: Optional[str]) -> dict:
    """Reset ``state.turn.checked`` when a new turn begins (Bug 2).

    A turn boundary is a change in ``state.turn.turn_id``. When ``turn_id`` is
    given and differs from the one in state, the checked list is cleared so a
    task completed in an earlier turn never leaks into the next one. The
    UserPromptSubmit and session-start hooks pass the new turn id here too.
    Returns the mutated ``turn`` dict.
    """
    turn = state.get("turn")
    if not isinstance(turn, dict):
        turn = {}
        state["turn"] = turn
    if turn_id is not None:
        if turn.get("turn_id") != turn_id:
            turn["checked"] = []
            turn["turn_id"] = turn_id
    return turn


def refresh_snapshot(state: dict, project: Path, spec: str,
                     stdin: Optional[dict] = None,
                     turn_id: Optional[str] = None) -> dict:
    """Re-parse a spec's tasks.md, record newly checked ids, refresh snapshot.

    When a snapshot already exists for the spec, newly checked ids come from
    the diff against ``state.tasks_snapshot[spec]`` as before. When there is no
    snapshot yet (the first edit of the run), the pre-edit file state is not on
    disk anymore, so the baseline is the edit itself: for a ``str_replace`` the
    ``oldStr``/``newStr`` in ``stdin`` show which checkbox changed, and for an
    ``fs_write`` with no snapshot nothing is treated as newly checked (Bug 2).
    This stops every already-checked task from being counted as checked this
    turn on the first run.

    Newly checked ids are appended to ``state.turn.checked`` without
    duplicates. ``turn_id`` resets that list first when the turn changed
    (Bug 2). The snapshot is refreshed and the spec is remembered as the last
    touched one. Returns the mutated state (for ``update_json``).
    """
    project = Path(project)
    stdin = stdin if isinstance(stdin, dict) else {}

    turn = _reset_turn_if_new(state, turn_id)

    snapshots = state.get("tasks_snapshot")
    if not isinstance(snapshots, dict):
        snapshots = {}
        state["tasks_snapshot"] = snapshots
    previous = snapshots.get(spec)
    have_snapshot = isinstance(previous, dict)
    if not have_snapshot:
        previous = {}

    current = _snapshot_for(project, spec)

    if have_snapshot:
        # A snapshot exists: diff the current file against it. Only an id that
        # was present and unchecked counts. An id new in the file (added
        # already checked) just enters the snapshot.
        newly_checked = [
            task_id
            for task_id, checked in current.items()
            if checked and previous.get(task_id) is False
        ]
    else:
        # No snapshot yet: the pre-edit file is gone, so the only reliable
        # signal of what this turn checked is the edit itself (Bug 2).
        newly_checked = [
            task_id
            for task_id in _newly_checked_from_edit(stdin)
            if current.get(task_id, False)
        ]

    checked = turn.get("checked")
    if not isinstance(checked, list):
        checked = []
    for task_id in newly_checked:
        if task_id not in checked:
            checked.append(task_id)
    turn["checked"] = checked

    snapshots[spec] = current
    state["last_tasks_spec"] = spec
    return state


def sync_checked_at_stop(state: dict, project: Path) -> dict:
    """Stop-time diff of every spec's tasks.md against the snapshot.

    The post-tool hook only sees tools whose input names tasks.md. A box
    flipped by a shell command (``sed -i``) is invisible to it. At Stop this
    diff is the authority: for each spec with a snapshot, every id that went
    from unchecked to checked is added to ``state.turn.checked`` (never twice),
    and the snapshot is refreshed. A spec with no snapshot yet only gets a
    baseline, nothing is counted. Returns the mutated state.
    """
    project = Path(project)
    turn = state.get("turn")
    if not isinstance(turn, dict):
        turn = {}
        state["turn"] = turn
    snapshots = state.get("tasks_snapshot")
    if not isinstance(snapshots, dict):
        snapshots = {}
        state["tasks_snapshot"] = snapshots
    checked = turn.get("checked")
    if not isinstance(checked, list):
        checked = []

    for spec in _list_specs(project):
        current = _snapshot_for(project, spec)
        previous = snapshots.get(spec)
        if isinstance(previous, dict):
            for task_id, is_checked in current.items():
                if is_checked and previous.get(task_id) is False \
                        and task_id not in checked:
                    checked.append(task_id)
        snapshots[spec] = current
    turn["checked"] = checked
    return state


def rebaseline_snapshots(state: dict, project: Path) -> dict:
    """Re-baseline every spec's tasks.md snapshot, counting nothing as checked.

    Called at UserPromptSubmit and SessionStart. A checkbox a human ticked by
    hand between two agent turns must not be credited to the next turn, so the
    snapshot is replaced by the file as it is now and ``state.turn.checked`` is
    left alone. Post-tool and Stop keep the diff. Returns the mutated state.
    """
    project = Path(project)
    snapshots = state.get("tasks_snapshot")
    if not isinstance(snapshots, dict):
        snapshots = {}
        state["tasks_snapshot"] = snapshots
    for spec in _list_specs(project):
        snapshots[spec] = _snapshot_for(project, spec)
    return state


# --- record building and the Stop hook (task 3.3) --------------------------


def _round_credits(value: Optional[float]) -> Optional[float]:
    """Round a credit figure to 4 decimals, passing None through (design)."""
    if value is None:
        return None
    return round(float(value), 4)


def _date_of(turns: list[dict]) -> Optional[str]:
    """Local date of the last turn end, or None when no turn has an end.

    The ``end`` fields are ISO 8601 strings. The newest end wins. The date is
    the local calendar date of that moment (design: ``date`` is the local date
    of the last turn end).
    """
    best: Optional[datetime] = None
    for turn in turns:
        end = turn.get("end")
        if not isinstance(end, str) or not end:
            continue
        text = end[:-1] + "+00:00" if end.endswith("Z") else end
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if best is None or parsed > best:
            best = parsed
    if best is None:
        return None
    return best.astimezone().date().isoformat()


def _iso(value: object) -> Optional[str]:
    """Normalise a datetime or ISO string to an ISO 8601 string, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, str) and value:
        return value
    return None


def turn_entry(turn, estimate: Optional[float], share: float) -> dict:
    """Build one turn entry for a record from a ``sessions.Turn`` (R1.4).

    ``estimate`` is the running estimate recorded for the turn (requests times
    the average request price) and stays set even after the actual lands.
    ``actual`` is the priced cost of a closed turn, or None while unpriced.
    ``share`` is this turn's fraction of the task when several tasks were
    checked in one turn, so weighted sums stay correct.
    """
    minutes = None
    if turn.start is not None and turn.end is not None:
        minutes = round((turn.end - turn.start).total_seconds() / 60.0, 4)
    actual = turn.cost if turn.state == "closed" else None
    return {
        "session_id": turn.session_id,
        "turn_id": turn.turn_id,
        "requests": turn.requests,
        "estimate": _round_credits(estimate),
        "actual": _round_credits(actual),
        "minutes": minutes,
        "start": _iso(turn.start),
        "end": _iso(turn.end),
        "share": round(float(share), 4),
        "unpriced_reason": turn.unpriced_reason if turn.state == "closed" else None,
    }


def _sum_weighted(entries: list[dict], key: str) -> Optional[float]:
    """Sum ``key`` over entries weighted by ``share``. None if all are None."""
    total = 0.0
    seen = False
    for entry in entries:
        value = entry.get(key)
        if isinstance(value, (int, float)):
            total += float(value) * float(entry.get("share", 1.0))
            seen = True
    return _round_credits(total) if seen else None


def _record_id(ref: "TaskRef") -> str:
    """The record id: ``<spec>/<task_id>`` or ``unknown/<turn_id>`` (R2.4)."""
    if ref.task_id == "unknown":
        turn_id = ref.turn_id or "unknown"
        return f"unknown/{turn_id}"
    spec = ref.spec or "unknown"
    return f"{spec}/{ref.task_id}"


def build_record(
    ref: "TaskRef",
    turn,
    *,
    engine: str,
    model: Optional[str],
    estimate: Optional[float],
    share: float = 1.0,
    outcome: str = "finished",
    task_type: str = "other",
    existing: Optional[dict] = None,
) -> dict:
    """Build or extend a tombstone record for one closed turn (R1.4, R2.1).

    The record carries the task metadata and a list of turn entries. Top-level
    ``requests``, ``estimate``, ``actual`` and ``minutes`` are sums over the
    entries weighted by ``share``; ``actual`` is None while any entry is
    unpriced so the human view shows ``est.`` only (R4.4). ``existing`` is the
    record already stored for this id, so a second turn of the same task is
    folded in rather than replacing the first. Upsert by id and by turn id is
    left to :func:`upsert_record`, which keeps a re-run idempotent.
    """
    entry = turn_entry(turn, estimate, share)

    entries: list[dict] = []
    if existing is not None:
        for stored in existing.get("turns") or []:
            if isinstance(stored, dict) and stored.get("turn_id") != entry["turn_id"]:
                entries.append(stored)
    entries.append(entry)

    total_requests = 0
    saw_requests = False
    for item in entries:
        value = item.get("requests")
        if isinstance(value, int):
            total_requests += value
            saw_requests = True

    total_estimate = _sum_weighted(entries, "estimate")
    total_minutes = _sum_weighted(entries, "minutes")

    # actual is None while any entry is still unpriced (R4.4).
    any_unpriced = any(item.get("actual") is None for item in entries)
    total_actual = None if any_unpriced else _sum_weighted(entries, "actual")

    record = {
        "id": _record_id(ref),
        "task_id": ref.task_id,
        "name": ref.name,
        "spec": ref.spec,
        "task_type": task_type,
        "engine": engine,
        "model": model,
        "requests": total_requests if saw_requests else None,
        "estimate": total_estimate,
        "actual": total_actual,
        "minutes": total_minutes,
        "outcome": outcome,
        "false_positive": existing.get("false_positive") if existing else None,
        "date": _date_of(entries),
        "turns": entries,
    }
    return record


# --- average request price window (R4.3) -----------------------------------

# The price window keeps at least this many requests of priced turns.
PRICE_WINDOW_REQUESTS = 50

# Fallback average request price in credits, used until the window has data
# and until caps.load_config (task 4) can read tombstone.toml. This mirrors
# the default of R3.2 (budget.avg_request_price).
DEFAULT_AVG_REQUEST_PRICE = 0.25

# Credits per counted V3 unit, used only until the project has its own unit
# calibration (state.avg_unit_price). Measured over 35 real V3 turns of this
# repo: 855 units, 135.91 credits, 0.159 credits per unit. A V3 unit is not a
# request, so the per-request price (0.25) overestimated a fresh project 4.6x
# (live: 17 units, est. 4.25, actual 0.927). Never used for V2.
DEFAULT_V3_UNIT_PRICE = 0.16


def update_price_window(state: dict, requests: Optional[int],
                        cost: Optional[float]) -> dict:
    """Fold one priced turn into ``state.price_window`` and recompute average.

    The window is a list of ``[requests, cost]`` per priced turn, newest last.
    Oldest turns are dropped while the remaining requests still total at least
    ``PRICE_WINDOW_REQUESTS`` (R4.3). ``avg_request_price`` is the summed cost
    over the summed requests of the window. A turn with no cost or no requests
    is ignored. Returns the mutated state.
    """
    if not isinstance(requests, int) or requests <= 0 or not isinstance(cost, (int, float)):
        return state

    window = state.get("price_window")
    if not isinstance(window, list):
        window = []
    window.append([int(requests), _round_credits(float(cost))])

    # Drop oldest entries while we can still keep at least the window size.
    total_requests = sum(item[0] for item in window if isinstance(item, list) and item)
    while len(window) > 1:
        first = window[0]
        if not isinstance(first, list) or not first:
            window.pop(0)
            continue
        if total_requests - first[0] >= PRICE_WINDOW_REQUESTS:
            total_requests -= first[0]
            window.pop(0)
        else:
            break

    sum_requests = sum(item[0] for item in window if isinstance(item, list) and item)
    sum_cost = sum(item[1] for item in window
                   if isinstance(item, list) and len(item) > 1
                   and isinstance(item[1], (int, float)))
    state["price_window"] = window
    if sum_requests > 0:
        state["avg_request_price"] = round(sum_cost / sum_requests, 6)
    return state


# --- V3 unit calibration (R4.1), shared by the watcher and the hooks -------

# Like the request price window: keep at least this many units of closed turns.
UNIT_WINDOW_UNITS = 50
UNIT_TURNS_KEPT = 200


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


def fold_unit_turn(state: dict, turn_id: str, units: int, cost: float) -> bool:
    """Fold a closed V3 turn into the unit calibration once per ``turn_id``.

    The dedupe list ``state.unit_turns`` is shared by the watcher and the
    hooks, so a turn seen by both is counted once. Returns True when the turn
    was folded, False when it was already known or not usable.
    """
    seen = state.get("unit_turns")
    seen = seen if isinstance(seen, list) else []
    folded = False
    if turn_id not in seen and isinstance(units, int) and units > 0 \
            and isinstance(cost, (int, float)):
        update_unit_window(state, units, cost)
        seen.append(turn_id)
        folded = True
    state["unit_turns"] = seen[-UNIT_TURNS_KEPT:]
    return folded


def closed_turn_units(session_path, turn) -> int:
    """Unit count (assistant events) of a closed V3 turn, 0 when unknown."""
    from . import sessions

    if session_path is None or getattr(turn, "engine", None) != "v3":
        return 0
    try:
        return sessions.assistant_counts(Path(session_path)).get(turn.turn_id, 0)
    except Exception:  # noqa: BLE001 - calibration is a nicety
        return 0


def fold_priced_turn(state: dict, turn, session_path=None) -> dict:
    """Fold a closed, priced turn into the price window and, for V3, the units."""
    update_price_window(state, turn.requests, turn.cost)
    if session_path is not None and isinstance(turn.cost, (int, float)):
        seen = state.get("unit_turns")
        if not (isinstance(seen, list) and turn.turn_id in seen):
            units = closed_turn_units(session_path, turn)
            if units > 0:
                fold_unit_turn(state, turn.turn_id, units, float(turn.cost))
    return state


def _avg_price(state: dict, config_default: float) -> float:
    """The average request price from state, or the config default."""
    value = state.get("avg_request_price") if isinstance(state, dict) else None
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return float(config_default)


def _recorded_turn_ids(store: dict) -> set[str]:
    """Every turn id already stored in any record."""
    ids: set[str] = set()
    for record in store.get("tombstones") or []:
        if not isinstance(record, dict):
            continue
        for turn in record.get("turns") or []:
            if isinstance(turn, dict):
                turn_id = turn.get("turn_id")
                if isinstance(turn_id, str):
                    ids.add(turn_id)
    return ids


def _newest_unrecorded_closed_turn(turns: list, recorded: set[str]):
    """The newest closed, not-yet-recorded turn in a session, or None.

    "Newest" is by end time, falling back to document order. A turn already in
    a record is skipped so re-running the hook does not double count.
    """
    candidates = [
        turn for turn in turns
        if turn.state == "closed" and turn.turn_id not in recorded
    ]
    if not candidates:
        return None

    def key(turn):
        return (turn.end is not None, turn.end or datetime.min.replace(tzinfo=timezone.utc))

    candidates.sort(key=key)
    return candidates[-1]


def _newest_unrecorded_turn(turns: list, recorded: set[str]):
    """The newest unrecorded turn in a session, closed or open, or None (Bug 1).

    When ``Stop`` fires, the ``usage_summary`` of the finishing turn may not be
    on disk yet, so that turn still parses as open. We must still record it
    (``actual = null``) and reconcile later, so the open turn is a candidate
    here. A closed turn is preferred when both exist for the same position; the
    sort keeps the newest turn, ordering open turns (no end) by document order
    after any ended turns.
    """
    candidates = [turn for turn in turns if turn.turn_id not in recorded]
    if not candidates:
        return None

    def key(item):
        index, turn = item
        has_end = turn.end is not None
        end = turn.end or datetime.min.replace(tzinfo=timezone.utc)
        return (has_end, end, index)

    ordered = sorted(enumerate(candidates), key=key)
    return ordered[-1][1]


def _existing_record(store: dict, record_id: str) -> Optional[dict]:
    for record in store.get("tombstones") or []:
        if isinstance(record, dict) and record.get("id") == record_id:
            return record
    return None


def on_turn_end(project: Path, stdin: dict, *,
                kiro_home: Optional[Path] = None,
                now: Optional[datetime] = None) -> Optional[dict]:
    """Body of ``tombstone hook stop``: record the newest closed turn (R2.1).

    Resolves the active session, takes the newest closed turn not already in a
    record, resolves the task, builds the record and upserts it into
    ``tombstones.json`` through the locked store. When the closing turn has a
    cost, it also folds the turn into the average request price window in
    ``state.json`` (R4.3). If the cost has not landed yet the turn is stored
    with ``actual = null`` (R4.2) and the watcher reconciles it later.

    Returns the record that was written, or None when there was nothing to
    record (no session, no unrecorded closed turn). Never raises for expected
    missing data; the caller logs and exits 0 on any unexpected error.

    The average request price comes from ``state.avg_request_price`` once the
    window has data, otherwise :data:`DEFAULT_AVG_REQUEST_PRICE`. Task 4 wires
    ``tombstone.toml`` in; until then the default stands in for its value.
    """
    from . import sessions

    project = Path(project)
    if now is None:
        now = datetime.now(timezone.utc)

    state_path = project / TOMBSTONE_DIR / "state.json"
    # The Stop diff is the authority for checked ids (shell edits are missed
    # by the post-tool hook). Persist it so the next turn does not see them.
    try:
        update_json(
            state_path,
            lambda s: sync_checked_at_stop(s, project),
            default={"version": 1},
        )
    except Exception:  # fail open: fall back to what the post-tool hook saw
        pass
    state, _valid = _read_json(state_path)
    if state is None:
        state = {}

    session_id = None
    for key in ("session_id", "sessionId"):
        value = stdin.get(key) if isinstance(stdin, dict) else None
        if isinstance(value, str) and value:
            session_id = value
            break
    cached_hash = state.get("v3_project_hash") if isinstance(state, dict) else None

    session_path = sessions.find_session(
        project, session_id, kiro_home=kiro_home, v3_project_hash=cached_hash
    )
    if session_path is None:
        return None

    session = sessions.parse_session(session_path)
    if session.engine is None or not session.turns:
        return None

    store = load_tombstones(project / "tombstones.json")
    recorded = _recorded_turn_ids(store)
    # Bug 1: record the newest unrecorded turn even when it is still open. At
    # Stop the usage_summary may not be on disk yet, so the finishing turn
    # parses as open; we write it now (actual = null) and reconcile fills the
    # cost in later.
    turn = _newest_unrecorded_turn(session.turns, recorded)
    if turn is None:
        return None

    refs = resolve_tasks(project, stdin if isinstance(stdin, dict) else {}, state)

    # Several boxes checked in one turn: equal split, one record each (R2.3).
    share = 1.0 / len(refs)

    avg_price = _avg_price(state, DEFAULT_AVG_REQUEST_PRICE)
    estimate = None
    if isinstance(turn.requests, int):
        estimate = turn.requests * avg_price

    outcome = str(stdin.get("outcome")) if isinstance(stdin, dict) \
        and isinstance(stdin.get("outcome"), str) else "finished"

    records: list[dict] = []
    for ref in refs:
        if ref.task_id == "unknown":
            # The session turn id keeps one record per turn. Without a prompt
            # name the record is named after the turn time (R2.4).
            ref = TaskRef(spec=ref.spec, task_id=ref.task_id,
                          name=ref.name or _iso(turn.start) or _iso(turn.end),
                          source=ref.source, turn_id=turn.turn_id)
        existing = _existing_record(store, _record_id(ref))
        records.append(build_record(
            ref,
            turn,
            engine=session.engine,
            model=turn.model or session.model,
            estimate=estimate,
            share=share,
            outcome=outcome,
            existing=existing,
        ))

    def write_all(store_value: dict) -> dict:
        for item in records:
            store_value = upsert_record(store_value, item)
        return store_value

    update_json(
        project / "tombstones.json",
        write_all,
        default=_empty_store(),
        pending_apply=apply_pending_record,
    )
    record = records[0]

    if turn.state == "closed" and turn.cost is not None \
            and isinstance(turn.requests, int):
        # The cost was already on disk: fold the priced turn into the window.
        update_json(
            state_path,
            lambda s: fold_priced_turn(s, turn, session_path),
            default={"version": 1},
        )
        _settle_autopsy(project, kiro_home)
    elif turn.cost is None:
        # The cost has not landed yet. Start a detached reconcile child that
        # polls the session and fills actual once the usage_summary appears
        # (Bug 1). The Stop hook itself returns at once.
        spawn_reconcile(project, session.session_id, turn.turn_id,
                        kiro_home=kiro_home)

    return record


def _settle_autopsy(project: Path, kiro_home: Optional[Path]) -> None:
    """Append the actual cost to a waiting autopsy (R7.3). Never raises."""
    try:
        from . import autopsy
        autopsy.settle_autopsy(project, kiro_home=kiro_home)
    except Exception:  # noqa: BLE001 - the receipt is a nicety
        pass


def spawn_reconcile(project: Path, session_id: Optional[str],
                    turn_id: Optional[str],
                    kiro_home: Optional[Path] = None,
                    timeout_s: Optional[float] = None) -> None:
    """Start a detached ``tombstone hook reconcile`` child (Bug 1).

    The child polls the session every 0.5 s for up to 30 s and fills ``actual``
    and ``requests`` once the ``usage_summary`` lands, using the same locked
    update. It is fully detached (``start_new_session=True``, stdout and stderr
    to devnull) so the Stop hook returns in well under 200 ms. Never raises; a
    failure to spawn is logged by the caller's try/except.
    """
    import subprocess
    import sys

    project = Path(project).resolve()
    args = [
        os.path.abspath(sys.executable), "-m", "tombstone", "hook", "reconcile",
        "--session", session_id or "",
        "--turn", turn_id or "latest",
        "--project", str(project),
    ]
    if kiro_home is not None:
        args += ["--kiro-home", str(Path(kiro_home).resolve())]
    if timeout_s is not None:
        # Tests pass a short value so no child polls for the default 30 s.
        args += ["--timeout", str(float(timeout_s))]

    # The child's stdout and stderr go to reconcile.log so a crash before it
    # can log anything (an import error, a bad interpreter) is still visible.
    # Falls back to devnull when the log cannot be opened.
    sidecar = project / TOMBSTONE_DIR
    sink = None
    try:
        if guard(project, sidecar / RECONCILE_LOG):
            sidecar.mkdir(parents=True, exist_ok=True)
            sink = open_nofollow(sidecar / RECONCILE_LOG, "ab")
    except OSError:
        sink = None
    try:
        # A new session (setsid) takes the child out of the hook runner's and
        # kiro-cli's process group. close_fds drops every inherited fd, stdin
        # is devnull, and the cwd is the project root so the child never holds
        # a directory the parent may remove. The interpreter path is absolute.
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=sink if sink is not None else subprocess.DEVNULL,
            stderr=sink if sink is not None else subprocess.DEVNULL,
            cwd=str(project) if project.is_dir() else None,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as error:
        reconcile_log(project, f"spawn failed for turn {turn_id}: {error}")
    finally:
        # The child holds its own copy of the fd; closing ours is safe.
        if sink is not None:
            try:
                sink.close()
            except OSError:
                pass


# Child log inside .tombstone/. One line per start, fill and timeout.
RECONCILE_LOG = "reconcile.log"


def reconcile_log(project: Path, message: str) -> None:
    """Append one timestamped line to ``reconcile.log``. Never raises."""
    sidecar = Path(project) / TOMBSTONE_DIR
    try:
        if not guard(project, sidecar / RECONCILE_LOG):
            return
        sidecar.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        append_nofollow(sidecar / RECONCILE_LOG,
                        f"{stamp} pid {os.getpid()} {message}\n")
    except OSError:
        pass


def _fill_turn(project: Path, turn, session_path=None) -> Optional[dict]:
    """Write a priced turn into its record and fold it into the price window.

    Shared by the reconcile child and the safety net. ``turn`` must be closed
    with a cost. ``requests`` comes from the turn's ``requestIds`` (the
    session parser), not from the open-turn estimate stored at Stop. With a
    ``session_path`` a V3 turn also calibrates the unit price (R4.1).
    """
    record = _apply_reconciled_turn(project, turn)
    if record is not None and isinstance(turn.requests, int):
        update_json(
            project / TOMBSTONE_DIR / "state.json",
            lambda s: fold_priced_turn(s, turn, session_path),
            default={"version": 1},
        )
    return record


# The safety net looks at no more than this many records per call.
PENDING_SCAN_LIMIT = 5


def _record_recency(item: tuple[int, dict]) -> tuple[str, int]:
    """Sort key: newest turn time first by string, list order breaks ties."""
    index, record = item
    stamps = []
    for entry in record.get("turns") or []:
        if isinstance(entry, dict):
            for key in ("end", "start"):
                value = entry.get(key)
                if isinstance(value, str):
                    stamps.append(value)
    return (max(stamps) if stamps else "", index)


def reconcile_pending(project: Path, *, kiro_home: Optional[Path] = None,
                      limit: int = PENDING_SCAN_LIMIT) -> int:
    """Fill records left with ``actual = null`` whose cost is now on disk.

    The safety net for a reconcile child that never ran. It needs no
    background process: every hook entry and the ``cost`` and ``graveyard``
    commands call it first. The scan is read-only: ``tombstones.json`` is read
    without the lock, and the store is written (locked) only when a cost is
    actually found. At most ``limit`` of the newest null records are looked
    at, and only turns that are not already marked unpriced. With nothing to
    do it costs one small JSON read. Returns the number of turns filled.
    Never raises.
    """
    from . import sessions

    project = Path(project)
    try:
        store, _valid = _read_json(project / "tombstones.json")
        if not store:
            return 0
        records = store.get("tombstones")
        if not isinstance(records, list):
            return 0

        pending = [
            (index, record) for index, record in enumerate(records)
            if isinstance(record, dict) and record.get("actual") is None
            and any(isinstance(e, dict) and e.get("actual") is None
                    and not e.get("unpriced_reason")
                    for e in record.get("turns") or [])
        ]
        if not pending:
            return 0
        pending.sort(key=_record_recency, reverse=True)

        state, _ok = _read_json(project / TOMBSTONE_DIR / "state.json")
        cached_hash = state.get("v3_project_hash") if isinstance(state, dict) else None

        filled = 0
        done: set[tuple] = set()
        for _index, record in pending[:max(0, limit)]:
            for entry in record.get("turns") or []:
                if not isinstance(entry, dict) or entry.get("actual") is not None \
                        or entry.get("unpriced_reason"):
                    continue
                turn_id = entry.get("turn_id")
                session_id = entry.get("session_id")
                if not isinstance(turn_id, str) or (session_id, turn_id) in done:
                    continue
                path = sessions.find_session(
                    project, session_id if isinstance(session_id, str) else None,
                    kiro_home=kiro_home, v3_project_hash=cached_hash,
                )
                if path is None:
                    continue
                for turn in sessions.parse_session(path).turns:
                    if turn.turn_id == turn_id:
                        if turn.state == "closed" and turn.cost is not None \
                                and _fill_turn(project, turn, path) is not None:
                            filled += 1
                            # One fill updates every record of a split turn.
                            done.add((session_id, turn_id))
                            reconcile_log(
                                project,
                                f"safety net filled turn {turn_id} actual "
                                f"{_round_credits(turn.cost)} requests {turn.requests}",
                            )
                        break
        return filled
    except Exception as error:  # noqa: BLE001 - the net must never break a hook
        reconcile_log(project, f"safety net error: {error}")
        return 0


def reconcile_turn(project: Path, session_id: Optional[str], turn_id: str,
                   *, kiro_home: Optional[Path] = None,
                   timeout_s: float = 30.0, interval_s: float = 0.5,
                   sleep=None, clock=None) -> Optional[dict]:
    """Poll a session until a turn is priced, then fill actual and requests.

    Re-reads the session every ``interval_s`` seconds for up to ``timeout_s``.
    Once the target turn is closed with a cost, the stored record for that turn
    is updated through the locked store: the turn entry's ``actual`` and
    ``requests`` are refreshed and the record totals recomputed, and the turn
    is folded into the average price window. Returns the updated record, or
    None on timeout. On timeout a single line is logged to ``errors.log`` and
    ``actual`` is left null (Bug 1). Never raises for expected missing data.

    ``sleep`` and ``clock`` are injection points for the tests so a replay does
    not wait in real time.
    """
    from . import sessions

    project = Path(project)
    if sleep is None:
        import time as _time
        sleep = _time.sleep
        clock = _time.monotonic
    if clock is None:
        import time as _time
        clock = _time.monotonic

    reconcile_log(project, f"start session {session_id or 'unknown'} "
                           f"turn {turn_id}")
    deadline = clock() + timeout_s
    first = True
    while True:
        if not first:
            sleep(interval_s)
        first = False

        state_path = project / TOMBSTONE_DIR / "state.json"
        state, _valid = _read_json(state_path)
        if state is None:
            state = {}
        cached_hash = state.get("v3_project_hash") if isinstance(state, dict) else None

        session_path = sessions.find_session(
            project, session_id, kiro_home=kiro_home, v3_project_hash=cached_hash
        )
        turn = None
        if session_path is not None:
            session = sessions.parse_session(session_path)
            for candidate in session.turns:
                if candidate.turn_id == turn_id:
                    turn = candidate
                    break

        if turn is not None and turn.state == "closed" and turn.cost is not None:
            record = _fill_turn(project, turn, session_path)
            if record is not None:
                reconcile_log(project, f"filled turn {turn_id} actual "
                                       f"{_round_credits(turn.cost)} requests "
                                       f"{turn.requests}")
                _settle_autopsy(project, kiro_home)
            else:
                reconcile_log(project, f"turn {turn_id} priced but no record "
                                       "holds it")
            return record

        if clock() >= deadline:
            reconcile_log(project, f"timed out for turn {turn_id}, actual "
                                   "left null")
            _log_reconcile_timeout(project, session_id, turn_id)
            return None


def _apply_reconciled_turn(project: Path, turn) -> Optional[dict]:
    """Refresh the stored turn entry with the priced turn's actual and requests.

    Finds the record holding ``turn.turn_id`` and rebuilds its turn entry from
    the now-closed turn, keeping the entry's recorded ``estimate`` and
    ``share``. Record totals are recomputed so ``actual`` lands once the turn
    is priced. Returns the updated record, or None when no record holds the
    turn (nothing to reconcile).
    """
    tombstones_path = project / "tombstones.json"
    updated: dict = {}

    def apply(store: dict) -> dict:
        updated.clear()
        records = store.get("tombstones")
        if not isinstance(records, list):
            return store
        # A split turn has one entry per task record, all with the same
        # session and turn id. Update every one, each keeping its own share.
        for record in records:
            if not isinstance(record, dict):
                continue
            entries = record.get("turns")
            if not isinstance(entries, list):
                continue
            touched = False
            for pos, entry in enumerate(entries):
                if not isinstance(entry, dict) or entry.get("turn_id") != turn.turn_id:
                    continue
                entry_session = entry.get("session_id")
                if entry_session and turn.session_id and entry_session != turn.session_id:
                    continue
                estimate = entry.get("estimate")
                share = entry.get("share", 1.0)
                entries[pos] = turn_entry(turn, estimate, share)
                touched = True
            if touched:
                _recompute_record_totals(record)
                if not updated:
                    updated.update(record)
        return store

    update_json(
        tombstones_path,
        apply,
        default=_empty_store(),
        pending_apply=apply_pending_record,
    )
    return updated or None


def _recompute_record_totals(record: dict) -> None:
    """Recompute top-level sums over a record's turn entries (R1.4)."""
    entries = [e for e in (record.get("turns") or []) if isinstance(e, dict)]

    total_requests = 0
    saw_requests = False
    for entry in entries:
        value = entry.get("requests")
        if isinstance(value, int):
            total_requests += value
            saw_requests = True

    record["requests"] = total_requests if saw_requests else None
    record["estimate"] = _sum_weighted(entries, "estimate")
    record["minutes"] = _sum_weighted(entries, "minutes")
    any_unpriced = any(entry.get("actual") is None for entry in entries)
    record["actual"] = None if any_unpriced else _sum_weighted(entries, "actual")
    record["date"] = _date_of(entries)


def _log_reconcile_timeout(project: Path, session_id: Optional[str],
                           turn_id: str) -> None:
    """Log one line when a reconcile child times out. Never raises (Bug 1)."""
    sidecar = project / TOMBSTONE_DIR
    try:
        if not guard(project, sidecar / "errors.log"):
            return
        sidecar.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        line = (f"{stamp} reconcile timed out for session "
                f"{session_id or 'unknown'} turn {turn_id}, actual left null\n")
        append_nofollow(sidecar / "errors.log", line)
    except OSError:
        pass


# --- graveyard (R2.6, R4.4) -------------------------------------------------

_GRAVEYARD_CSS = """
:root{
  --night:#060912;--dusk:#101a2c;--earth:#0a0e15;--earth-hi:#121925;
  --granite:#858a93;--granite-hi:#b9bdc3;--granite-lo:#4b5059;--cut:#22252b;
  --bone:#ece7da;--muted:#9aa3b0;--moss:#56704f;--ember:#ff3b3b;--lantern:#e3b26a;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --grain:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 .55 0'/></filter><rect width='160' height='160' filter='url(%23n)'/></svg>");
}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--earth);color:var(--bone);font-family:var(--serif);
  font-variant-numeric:lining-nums tabular-nums;line-height:1.5;overflow-x:hidden}
a{color:var(--lantern)}
:focus-visible{outline:2px solid var(--lantern);outline-offset:4px}

/* the scene */
.sky{position:relative;overflow:hidden;padding:7vh 6vw 150px;
  background:
    radial-gradient(ellipse 38% 42% at 80% 20%,rgba(190,205,230,.16),transparent 70%),
    linear-gradient(var(--night) 0%,var(--dusk) 78%,#141f33 100%)}
.stars{position:absolute;inset:0 0 40% 0;opacity:.7;
  background-image:
    radial-gradient(1px 1px at 12% 18%,#fff,transparent),radial-gradient(1px 1px at 27% 8%,#dfe6f5,transparent),
    radial-gradient(1px 1px at 44% 26%,#fff,transparent),radial-gradient(1.5px 1.5px at 58% 12%,#e8eefc,transparent),
    radial-gradient(1px 1px at 66% 34%,#cfd8ea,transparent),radial-gradient(1px 1px at 91% 44%,#fff,transparent),
    radial-gradient(1px 1px at 7% 52%,#cfd8ea,transparent),radial-gradient(1px 1px at 36% 46%,#fff,transparent),
    radial-gradient(1.5px 1.5px at 73% 6%,#fff,transparent),radial-gradient(1px 1px at 52% 54%,#dfe6f5,transparent)}
.moon{position:absolute;right:13vw;top:11vh;width:clamp(76px,9vw,150px);aspect-ratio:1;border-radius:50%;
  background:radial-gradient(circle at 36% 34%,#fbf8ee,#d8dacf 56%,#aeb2aa 100%);
  box-shadow:0 0 80px 26px rgba(214,222,238,.13),0 0 220px 90px rgba(150,170,210,.07)}
.moon::after{content:"";position:absolute;inset:0;border-radius:50%;
  background:radial-gradient(circle at 64% 60%,rgba(0,0,0,.10) 0 8%,transparent 9%),
             radial-gradient(circle at 40% 70%,rgba(0,0,0,.08) 0 6%,transparent 7%),
             radial-gradient(circle at 30% 40%,rgba(0,0,0,.06) 0 10%,transparent 11%)}
.hills{position:absolute;left:0;right:0;bottom:0;width:100%;height:150px}
.hills .far{fill:#0d1420}
.hills .near{fill:var(--earth)}
.intro{position:relative;max-width:min(1100px,100%)}
.kicker{margin:0 0 18px;color:var(--muted);font-size:17px;letter-spacing:.02em}
h1{font-weight:400;font-size:clamp(40px,5.6vw,96px);line-height:.98;letter-spacing:-.015em;margin:0 0 22px;max-width:11em;text-wrap:balance;
  text-shadow:0 2px 30px rgba(0,0,0,.5)}
.lede{font-size:clamp(17px,1.6vw,21px);color:#c9cfd9;margin:0 0 28px;max-width:34em}
.proof{display:flex;flex-wrap:wrap;gap:20px 0;margin:0}
.proof div{display:flex;flex-direction:column-reverse;padding:2px 30px 2px 0;margin-right:30px;
  border-right:1px solid rgba(236,231,218,.16);max-width:15em}
.proof div:last-child{border-right:0}
.proof dd{margin:0;font-size:clamp(28px,2.6vw,40px);line-height:1.05;color:var(--bone)}
.proof dt{color:var(--muted);font-size:15px;line-height:1.3;margin-top:6px}
.proof .red dd{color:var(--ember)}

/* the ground */
.ground{position:relative;margin-top:-70px;padding:0 5vw 90px;
  background:linear-gradient(var(--earth),#080b11 60%,var(--earth))}
.field{max-width:1500px;margin:0 auto;display:flex;flex-wrap:wrap;align-items:flex-end;gap:84px 56px;padding-top:0}
.plot{position:relative;display:flex;flex-direction:column;align-items:stretch;max-width:100%}
.row{position:relative;display:flex;align-items:flex-end;gap:16px;padding:0 18px 14px}
.row::after{content:"";position:absolute;left:-8px;right:-8px;bottom:0;height:30px;border-radius:50% 50% 0 0/100% 100% 0 0;z-index:0;
  background:radial-gradient(ellipse 60% 100% at 50% 100%,rgba(86,112,79,.30),transparent 75%),
             linear-gradient(#141b26,var(--earth) 80%)}
.plaque{margin:14px 0 0 18px;font-size:15px;line-height:1.35;max-width:24em}
.plaque b{font-weight:400;color:var(--bone);font-size:17px}
.plaque .num{color:var(--lantern);margin-right:10px;opacity:.85}
.plaque span.sum{display:block;color:var(--muted);font-size:14px}
.section-note{margin:40px 0 0;color:var(--muted);font-size:16px;max-width:46em}
.section-note h2{font-weight:400;font-size:clamp(24px,2.2vw,32px);color:var(--bone);margin:0 0 6px}
.wide{flex-basis:100%}
.row-label{flex:0 0 100%;margin:0;font-size:15px;color:var(--muted)}
.section-note.wide{max-width:none;padding-right:max(0px,calc(100% - 46em))}

/* stones: lit by the moon from the upper right */
.stone{--h:calc(var(--hh) * 1.08);position:relative;z-index:1;width:calc(var(--h) * .66);height:var(--h);min-width:104px;overflow:visible;
  appearance:none;border:0;padding:18px 10px 16px;margin:0;cursor:pointer;color:var(--cut);
  font:inherit;text-align:center;display:flex;flex-direction:column;align-items:center;
  border-radius:50% 50% 5px 5px / 30% 30% 5px 5px;
  background:
    var(--grain),
    radial-gradient(ellipse 70% 36% at 70% 10%,rgba(255,255,255,.30),transparent 70%),
    linear-gradient(255deg,var(--granite-hi),var(--granite) 42%,var(--granite-lo) 100%);
  background-blend-mode:overlay,normal,normal;
  box-shadow:inset 7px 0 12px rgba(0,0,0,.28),inset -3px 0 5px rgba(255,255,255,.10),0 2px 0 #2b3038;
  transform-origin:bottom center;transition:transform .25s ease,filter .25s ease}
.stone::before{content:"";position:absolute;left:-10%;right:-10%;bottom:-8px;height:12px;border-radius:2px;z-index:-1;
  background:linear-gradient(#5d626b,#3a3e46)}
.stone::after{content:"";position:absolute;right:62%;bottom:-6px;width:85%;height:16px;border-radius:50%;z-index:-2;
  background:radial-gradient(ellipse at 70% 50%,rgba(0,0,0,.65),transparent 70%);transform:skewX(40deg)}
.stone:hover{transform:rotate(-1deg);filter:brightness(1.08)}
.stone .tid{font-size:13px;letter-spacing:.08em;opacity:.8}
.stone .cost{font-size:max(22px,calc(var(--h) * .16));line-height:1.02;margin-top:auto;
  text-shadow:0 1px 0 rgba(255,255,255,.35),0 -1px 0 rgba(0,0,0,.45)}
.stone .unit{font-size:13px;opacity:.8}
.stone .epi{font-size:14px;max-width:100%;overflow-wrap:anywhere;line-height:1.22;margin:auto 0 0;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.stone .tid,.stone .unit,.stone .epi{text-shadow:0 1px 0 rgba(255,255,255,.28)}

/* unmarked graves: low slabs in the fog, the name lives on the card */
.unmarked .row{flex-wrap:wrap;row-gap:46px}
.unmarked .stone{--h:calc(var(--hh) * .62);width:calc(var(--hh) * .5);min-width:92px;border-radius:10px 10px 3px 3px;
  padding:12px 8px 10px;filter:brightness(.72) saturate(.6)}
.unmarked .stone:hover{filter:brightness(.9) saturate(.7)}
.unmarked .stone .epi,.unmarked .stone .unit{display:none}
.unmarked .stone .tid{font-size:12px}

/* stopped by the breaker: the only red on the page */
.stopped .row{flex-wrap:wrap;row-gap:80px;padding-top:80px}
.stopped .row::after{background:radial-gradient(ellipse 60% 100% at 50% 100%,rgba(255,59,59,.20),transparent 75%),
             linear-gradient(#1a1418,var(--earth) 80%)}
.stopped .stone{background:
    var(--grain),
    radial-gradient(ellipse 70% 36% at 70% 10%,rgba(255,255,255,.22),transparent 70%),
    linear-gradient(255deg,#9a9596,#6e6a6d 42%,#433f46);
  background-blend-mode:normal,overlay,normal,normal}
.stopped .stone::before{background:linear-gradient(#5a3a3c,#2c1c1f);box-shadow:0 0 22px 4px rgba(255,59,59,.35)}
.stopped .stone .cost{color:#4a0d0d}

/* one orchestrated moment: the fog lifts once on load */
.fog{position:fixed;inset:0;pointer-events:none;z-index:5;
  background:radial-gradient(ellipse 80% 34% at 30% 76%,rgba(200,210,225,.24),transparent 70%),
             radial-gradient(ellipse 70% 28% at 72% 88%,rgba(200,210,225,.18),transparent 70%)}
@media (prefers-reduced-motion:no-preference){
  .fog{animation:clear 3.2s ease-out .4s forwards}
  @keyframes clear{to{opacity:0;transform:translateX(16%)}}
}
@media (prefers-reduced-motion:reduce){.fog{display:none}.stone{transition:none}.stone:hover{transform:none}}

/* the card and the register */
dialog{max-width:440px;width:calc(100% - 32px);border:0;padding:26px 28px;border-radius:3px;
  background:#ebe5d4 var(--grain);background-blend-mode:multiply;color:#1e1c18;font-family:var(--serif);box-shadow:0 30px 70px rgba(0,0,0,.7)}
dialog::backdrop{background:rgba(3,5,10,.74)}
dialog h3{font-weight:400;font-size:24px;margin:0 0 4px;line-height:1.2}
dialog .meta{color:#5b574d;margin:0 0 16px;font-size:15px}
dialog dl{display:grid;grid-template-columns:auto 1fr;gap:6px 18px;margin:0 0 18px}
dialog dt{color:#5b574d}
dialog dd{margin:0;text-align:right}
dialog .prompt{font-size:15px;color:#3d392f;margin:0 0 12px;font-style:italic;overflow-wrap:anywhere}
dialog .gap{font-size:15px;color:#3d392f;margin:0 0 18px;border-left:2px solid #b49a6a;padding-left:10px}
dialog{max-height:calc(100vh - 32px);overflow-y:auto}
dialog .receipt{border-top:1px solid #b49a6a;margin:0 0 18px;padding-top:12px}
dialog .receipt h4{font-weight:400;font-size:18px;margin:0 0 8px}
dialog .receipt p{margin:0 0 8px;font-size:15px}
dialog .receipt dl{margin:0 0 10px}
dialog #crt{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;color:#8f1d1d;
  background:rgba(255,59,59,.08);border-left:2px solid #c23a3a;margin:0 0 10px;padding:8px 10px;
  white-space:pre-wrap;overflow-wrap:anywhere}
dialog #crc{margin:0;padding-left:22px;font-size:14px;color:#3d392f}
dialog #crc li{overflow-wrap:anywhere}
dialog button{font:inherit;font-size:15px;padding:8px 18px;border:1px solid #1e1c18;background:transparent;border-radius:2px;cursor:pointer}
.register{max-width:1240px;margin:0 auto;padding:30px 5vw 80px;border-top:1px solid #1a2230}
.register h2{font-weight:400;font-size:clamp(26px,2.4vw,34px);margin:0 0 6px}
.register p{color:var(--muted);margin:0 0 16px}
.controls{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 14px;font-family:var(--sans);font-size:14px;color:var(--muted)}
.controls input{font:inherit;padding:9px 11px;background:#0f1520;color:var(--bone);border:1px solid #2a3546;border-radius:2px;min-width:220px;max-width:100%}
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:15px}
th,td{padding:9px 10px;border-bottom:1px solid #18202d;text-align:left;white-space:nowrap}
td.name{white-space:normal;min-width:220px}
th button{font:inherit;color:var(--muted);background:none;border:0;padding:0;cursor:pointer}
th button[aria-sort]{color:var(--bone)}
td.n,th.n{text-align:right}
tr.killed td{color:#ffb3b3}
tr[hidden]{display:none}
footer{max-width:1240px;margin:0 auto;padding:0 5vw 56px;color:var(--muted);font-size:14px}

@media (max-width:640px){
  .sky{padding:56px 16px 150px;min-height:0}
  .hills{height:140px}
  .moon{right:16px;top:28px}
  .kicker{font-size:15px;max-width:15em}
  .proof div{border-right:0;margin-right:0;padding-right:22px;flex-basis:50%}
  .ground,.register,footer{padding-left:16px;padding-right:16px}
  .field{gap:60px 20px}
  .row{flex-wrap:wrap;row-gap:40px;gap:14px;padding:0 6px 14px}
  .stone{--h:calc(var(--hh) * .9);min-width:120px}
  .unmarked .stone{min-width:96px}
  .stone .tid,.stone .unit,.unmarked .stone .tid{font-size:14px}
}
""".strip()

# R2.10: five headstone shapes, four cracks for stopped stones. The plinth
# band (--band) sits below the stone inside every clip-path so the plinth is
# not cut; stopped stones get a wider and deeper band so their red base glow
# shows the same on every shape.
_G_SHAPES = ("arch", "slab", "cross", "gothic", "shoulder")

_G_CRACKS = (
    "M30 0 L25 24 L35 40 L21 66 L28 94 L16 140|M25 24 L13 30 M35 40 L46 49 M28 94 L40 104",
    "M72 0 L66 18 L74 36 L60 58 L66 80 L52 104 L58 140|M66 18 L80 22 M60 58 L48 62 M52 104 L40 112",
    "M0 52 L18 56 L30 48 L46 60 L58 54 L74 66 L100 62|M30 48 L28 36 M58 54 L62 42 M74 66 L78 80",
    "M48 0 L52 20 L44 34 L56 52|M56 52 L70 74 L64 96 L80 140|M56 52 L38 76 L44 98 L26 140",
)


def _g_crack_css() -> str:
    rules = []
    for k, spec in enumerate(_G_CRACKS):
        main, *rest = spec.split("|")
        paths = f"<path d='{main}' fill='none' stroke='%23140e10' stroke-width='2.4'/>"
        for extra in rest:
            paths += f"<path d='{extra}' fill='none' stroke='%23140e10' stroke-width='1.4'/>"
        url = ("url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' "
               f"viewBox='0 0 100 140' preserveAspectRatio='none'>{paths}</svg>\") "
               "center/100% 100% no-repeat")
        rules.append(
            f".stopped .stone.crack-{k}{{background:{url},var(--grain),"
            "radial-gradient(ellipse 70% 36% at 70% 10%,rgba(255,255,255,.22),transparent 70%),"
            "linear-gradient(255deg,#9a9596,#6e6a6d 42%,#433f46);"
            "background-blend-mode:normal,overlay,normal,normal}")
    return "\n".join(rules)


_GRAVEYARD_CSS_V4 = """
/* v4: more room, at most eight stopped stones in a line */
.field{max-width:1700px;gap:96px 72px}
.row{gap:44px}
.stopped .row{gap:84px 96px;max-width:1660px;padding-top:40px}
.unmarked .row{gap:46px 40px}
/* v4: five headstone shapes; height still is the price */
.stone{--band:112% 100%,112% calc(100% + 14px),-12% calc(100% + 14px),-12% 100%}
.stopped .stone{--band:150% 100%,150% calc(100% + 40px),-50% calc(100% + 40px),-50% 100%}
.stone.shape-arch{border-radius:50% 50% 5px 5px / 30% 30% 5px 5px}
.stone.shape-slab{border-radius:3px;clip-path:polygon(7% 0,93% 0,100% 5%,100% 100%,var(--band),0 100%,0 5%)}
.stone.shape-gothic{border-radius:0 0 4px 4px;padding-top:30px;
  clip-path:polygon(0 24%,14% 9%,32% 2%,50% 0,68% 2%,86% 9%,100% 24%,100% 100%,var(--band),0 100%)}
.stone.shape-shoulder{border-radius:3px 3px 4px 4px;padding-top:24px;
  clip-path:polygon(0 12%,12% 12%,16% 4%,30% 0,70% 0,84% 4%,88% 12%,100% 12%,100% 100%,var(--band),0 100%)}
.stone.shape-cross{width:calc(var(--h) * .72);border-radius:0;padding-top:calc(var(--h) * .47);
  clip-path:polygon(40% 0,60% 0,60% 12%,80% 12%,80% 27%,60% 27%,60% 44%,96% 44%,100% 47%,100% 100%,var(--band),0 100%,0 47%,4% 44%,40% 44%,40% 27%,20% 27%,20% 12%,40% 12%)}
.stone.shape-cross .tid{order:0}
.stone.shape-cross .epi{display:none}
.unmarked .stone.shape-gothic,.unmarked .stone.shape-shoulder{padding-top:16px}
.unmarked .stone.shape-cross{padding-top:calc(var(--h) * .47)}
@media (max-width:640px){
  .row{gap:28px}
  .stopped .row{gap:56px 40px}
}
/* v5: first screen, tagline above the headline, legend and links below the figures */
.intro .tagline{margin:0 0 16px}
.intro .legend,.intro .links{margin:22px 0 0}
.intro .links a{color:var(--bone)}
""".strip()

_GRAVEYARD_CSS = _GRAVEYARD_CSS + "\n" + _g_crack_css() + "\n" + _GRAVEYARD_CSS_V4

# Detail card, register sort and register filter only. The headstones
# themselves are plain HTML and need no script.
_GRAVEYARD_JS = """
(function(){
var card=document.getElementById("card");
var ground=document.getElementById("ground");
if(card&&ground&&typeof card.showModal==="function"){
  var fields=[["Actual","actual"],["Estimate during the task","estimate"],["Requests","requests"],
              ["Minutes","minutes"],["Model","model"],["Engine","engine"]];
  ground.addEventListener("click",function(e){
    var b=e.target.closest?e.target.closest(".stone"):null;
    if(!b){return;}
    document.getElementById("ct").textContent=b.getAttribute("data-title")||"";
    document.getElementById("cm").textContent=b.getAttribute("data-meta")||"";
    document.getElementById("cg").textContent=b.getAttribute("data-gap")||"";
    var cp=document.getElementById("cp");
    if(cp){cp.textContent=b.getAttribute("data-prompt")||"";cp.hidden=cp.textContent==="";}
    var dl=document.getElementById("cd");
    while(dl.firstChild){dl.removeChild(dl.firstChild);}
    fields.forEach(function(f){
      var dt=document.createElement("dt");dt.textContent=f[0];
      var dd=document.createElement("dd");dd.textContent=b.getAttribute("data-"+f[1])||"n/a";
      dl.appendChild(dt);dl.appendChild(dd);
    });
    var rc=document.getElementById("cr"),st=b.getAttribute("data-receipt");
    if(rc){
      rc.hidden=st===null;
      var yes=st==="yes";
      document.getElementById("crn").hidden=yes;
      document.getElementById("crb").hidden=!yes;
      var rd=document.getElementById("crd"),rl=document.getElementById("crc");
      while(rd.firstChild){rd.removeChild(rd.firstChild);}
      while(rl.firstChild){rl.removeChild(rl.firstChild);}
      if(yes){
        [["Reason","rc-reason"],["Estimate at the stop","rc-estimate"],["Kill","rc-kill"]].forEach(function(f){
          var dt=document.createElement("dt");dt.textContent=f[0];
          var dd=document.createElement("dd");dd.textContent=b.getAttribute("data-"+f[1])||"n/a";
          rd.appendChild(dt);rd.appendChild(dd);
        });
        var rt=document.getElementById("crt");
        rt.textContent=b.getAttribute("data-rc-refusal")||"";
        rt.hidden=rt.textContent==="";
        (b.getAttribute("data-rc-calls")||"").split("\\n").forEach(function(c){
          if(c===""){return;}
          var li=document.createElement("li");li.textContent=c;rl.appendChild(li);
        });
      }
    }
    card.showModal();
  });
}
var tb=document.getElementById("tb"),q=document.getElementById("q");
if(!tb){return;}
var rows=Array.prototype.slice.call(tb.querySelectorAll("tr"));
var heads=Array.prototype.slice.call(document.querySelectorAll("th button"));
var key="actual",dir=-1;
function val(tr,k){
  var raw=tr.getAttribute("data-"+k);
  if(raw===null||raw===""){return null;}
  var n=Number(raw);
  return (k==="actual"||k==="estimate"||k==="requests"||k==="minutes")?(isNaN(n)?null:n):raw;
}
function render(){
  var s=(q&&q.value||"").toLowerCase().trim();
  rows.sort(function(a,b){
    var x=val(a,key),y=val(b,key);
    if(x===null||y===null){return (x===null)-(y===null);}
    return (x<y?-1:(x>y?1:0))*dir;
  });
  rows.forEach(function(tr){
    tb.appendChild(tr);
    tr.hidden=s!==""&&(tr.getAttribute("data-text")||"").indexOf(s)===-1;
  });
  heads.forEach(function(b){
    if(b.getAttribute("data-k")===key){b.setAttribute("aria-sort",dir<0?"descending":"ascending");}
    else{b.removeAttribute("aria-sort");}
  });
}
heads.forEach(function(b){
  b.addEventListener("click",function(){
    var k=b.getAttribute("data-k");
    dir=(k===key)?-dir:-1;key=k;render();
  });
});
if(q){q.addEventListener("input",render);}
})();
""".strip()


def _g_num(value: object) -> Optional[float]:
    """A finite number from a record field, else None (bool is not a number)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return float(value)


def _g_credits(value: Optional[float]) -> str:
    """Credits with three decimals, so small prices stay visible."""
    return "n/a" if value is None else f"{value:.3f}"


def _g_cost(record: dict) -> tuple[Optional[float], str]:
    """The price a headstone shows and its label (R4.4).

    ``actual`` when the record has it, else the estimate labeled ``est.``,
    else no price at all (label ``unpriced``).
    """
    actual = _g_num(record.get("actual"))
    if actual is not None:
        return actual, "actual"
    estimate = _g_num(record.get("estimate"))
    if estimate is not None:
        return estimate, "est."
    return None, "unpriced"


def _g_str(record: dict, key: str, default: str = "") -> str:
    value = record.get(key)
    return value if isinstance(value, str) and value else default


def _g_task_id(record: dict) -> str:
    value = record.get("task_id")
    return str(value) if value is not None and str(value) != "" else "unknown"


def _g_height(cost: Optional[float], peak: float) -> int:
    """Headstone height in px, the mockup formula: 118 + 150 * sqrt(cost / peak)."""
    return round(118 + 150 * math.sqrt((cost or 0.0) / peak))


def _g_plot_key(task_id: str) -> str:
    return task_id.split(".")[0]


def _g_task_sort(task_id: str) -> list:
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.]", task_id)]


def _g_outcome_text(outcome: str) -> str:
    """The outcome as a person reads it: the breaker's ``killed`` reads "stopped"."""
    return "stopped" if outcome == "killed" else outcome


_G_FALLBACK_SHAPES = ("slab", "arch", "gothic", "shoulder")


def _g_row_shapes(records: list, peak: float, row_key: str) -> list:
    """Shape names for one row, left to right (R2.10).

    The shape cycle gives distinct raw neighbours. A cross under 170 px falls
    back to the first of slab, arch, gothic, shoulder that differs from the
    stone before it (final) and the stone after it (raw, or, when that one
    also falls back, it will adapt to this one).
    """
    salt = zlib.crc32(row_key.encode("utf-8"))
    raw = [_G_SHAPES[(i + salt) % len(_G_SHAPES)] for i in range(len(records))]
    short = [raw[i] == "cross" and _g_height(_g_cost(r)[0], peak) < 170
             for i, r in enumerate(records)]
    out: list = []
    for i, shape in enumerate(raw):
        if short[i]:
            avoid = {out[-1]} if out else set()
            if i + 1 < len(raw) and not short[i + 1]:
                avoid.add(raw[i + 1])
            shape = next(c for c in _G_FALLBACK_SHAPES if c not in avoid)
        out.append(shape)
    return out


def _g_receipt_attrs(record: dict, receipt: Optional[dict]) -> str:
    """Data attributes of the stop receipt for a stopped stone (R2.11). Escaped."""
    if record.get("outcome") == "finished":
        return ""
    esc = functools.partial(html.escape, quote=True)
    if not isinstance(receipt, dict):
        return 'data-receipt="no" '
    calls = "\n".join(f"{tool}: {title}" if title else tool
                      for tool, title in (receipt.get("calls") or [])[:10])
    estimate = receipt.get("estimate") or ""
    return (
        'data-receipt="yes" '
        f'data-rc-reason="{esc(receipt.get("reason") or "unknown")}" '
        f'data-rc-refusal="{esc(receipt.get("refusal") or "")}" '
        f'data-rc-estimate="{esc(estimate + " est." if estimate else "n/a")}" '
        f'data-rc-kill="{"ran" if receipt.get("kill_ran") else "did not run"}" '
        f'data-rc-calls="{esc(calls)}" '
    )


def _g_stop_reason(receipt: Optional[dict]) -> str:
    """The reason of a stop in plain words, from its receipt (R2.12)."""
    if not isinstance(receipt, dict):
        return "stopped"
    reason = str(receipt.get("reason") or "").lower()
    refusal = str(receipt.get("refusal") or "").lower()
    if "block" in reason or " blocked" in refusal:
        return "blocked before it started"
    if reason == "over_cap":
        return "over its cap"
    if reason == "stall":
        return "stalled"
    return "stopped"


def _g_stop_time(record: dict) -> str:
    """Stop time of a record: its first turn stamp, else its date (sortable text)."""
    stamps = [e.get("start") or e.get("end") or ""
              for e in (record.get("turns") or []) if isinstance(e, dict)]
    stamps = [s for s in stamps if isinstance(s, str) and s]
    return min(stamps) if stamps else _g_str(record, "date")


def _g_stop_order(stopped: list, receipts: dict) -> tuple:
    """Stopped records as (with a receipt, newest first; without a receipt) (R2.14)."""
    has = [r for r in stopped if isinstance(receipts.get(r.get("id")), dict)]
    none = [r for r in stopped if not isinstance(receipts.get(r.get("id")), dict)]
    return sorted(has, key=_g_stop_time, reverse=True), none


def _g_stop_numbers(stopped: list) -> dict:
    """Map ``id(record)`` of each stopped record with an unknown task id to N.

    N counts from the oldest by stop time (turn start, else the date), and
    the input order breaks ties, so the same input gives the same numbers.
    """
    def key(item: tuple) -> tuple:
        pos, record = item
        return (_g_stop_time(record), pos)

    unknown = [(i, r) for i, r in enumerate(stopped) if _g_task_id(r) == "unknown"]
    return {id(r): n for n, (_, r) in enumerate(sorted(unknown, key=key), 1)}


def _g_stone(record: dict, peak: float, index: int = 0, row_key: str = "",
             shape: Optional[str] = None, receipt: Optional[dict] = None,
             stop_no: Optional[int] = None) -> str:
    """One headstone as a ``<button>`` with plain text inside. Everything escaped.

    ``index`` is the position in its row and ``row_key`` the plot number,
    "unmarked" or "stopped" (R2.10): together they pick the shape and, for a
    stopped stone, the crack, so neighbours never share either.
    """
    esc = functools.partial(html.escape, quote=True)
    cost, label = _g_cost(record)
    task_id = _g_task_id(record)
    outcome = _g_str(record, "outcome", "unknown")
    name = _g_str(record, "name", "(unnamed)")
    live_stop = task_id == "unknown" and outcome != "finished"
    if live_stop:
        tid = f"Live stop {stop_no}" if stop_no else "Live stop"
    else:
        tid = "no task" if task_id == "unknown" else f"task {task_id}"
    epi = _g_stop_reason(receipt) if live_stop else name
    card_title = f"{tid}, {epi}" if live_stop else name
    prompt_attr = ""
    if live_stop:
        cut = len(name.split()) >= PROMPT_NAME_WORDS
        prompt_attr = f'data-prompt="{esc("Prompt: " + name + (chr(8230) if cut else ""))}" '
    estimate = _g_num(record.get("estimate"))
    actual = _g_num(record.get("actual"))
    requests = record.get("requests")
    if isinstance(requests, bool) or not isinstance(requests, int):
        requests = None
    minutes = _g_num(record.get("minutes"))
    date = _g_str(record, "date")
    source = _g_str(record, "source")
    height = _g_height(cost, peak)

    meta = (f"{tid if live_stop else ('No task' if task_id == 'unknown' else 'Task ' + task_id)}, "
            f"{_g_outcome_text(outcome)}")
    if date:
        meta += f", {date}"
    if source:
        meta += f", test project {source}"
    basis = "actual" if actual is not None else "est."
    if actual is not None and estimate is not None and actual > 0:
        pct = round(100 * (estimate - actual) / actual)
        gap = (f"The estimate was {abs(pct)} percent "
               f"{'above' if estimate >= actual else 'below'} the actual price.")
    else:
        gap = "The actual price had not arrived when this was written."

    unit = f"credits {label}"
    aria = f"{tid}, {_g_credits(cost)} credits {label}, {epi}"
    if shape is None:
        shape = _G_SHAPES[(index + zlib.crc32(row_key.encode("utf-8"))) % len(_G_SHAPES)]
        if shape == "cross" and height < 170:
            shape = "slab"
    cls = f"stone shape-{shape}"
    if outcome != "finished":
        cls += f" crack-{(index * 3 + 1) % 4}"
    return (
        f'<button type="button" class="{cls}" style="--hh:{height}px;--h:{height}px" '
        f'aria-label="{esc(aria)}" data-title="{esc(card_title)}" data-meta="{esc(meta)}" '
        f'data-actual="{esc(_g_credits(actual) + " credits actual" if actual is not None else "n/a, actual pending")}" '
        f'data-estimate="{esc(_g_credits(estimate) + " credits est." if estimate is not None else "n/a")}" '
        f'data-requests="{esc(str(requests) + " " + basis if requests is not None else "n/a")}" '
        f'data-minutes="{esc(f"{minutes:.1f} {basis}" if minutes is not None else "n/a")}" '
        f'data-model="{esc(_g_str(record, "model", "n/a"))}" '
        f'data-engine="{esc(_g_str(record, "engine", "n/a"))}" '
        f'{_g_receipt_attrs(record, receipt)}'
        f'{prompt_attr}'
        f'data-gap="{esc(gap)}">'
        f'<span class="tid">{esc(tid)}</span>'
        f'<span class="cost">{esc(_g_credits(cost))}</span>'
        f'<span class="unit">{esc(unit)}</span>'
        f'<span class="epi">{esc(epi)}</span></button>'
    )


def _g_sum_label(rows: list) -> str:
    """'4.250 credits actual', or 'est. and actual' when some price is still an estimate."""
    total = sum(_g_cost(r)[0] or 0.0 for r in rows)
    mixed = any(_g_cost(r)[1] != "actual" for r in rows)
    return f"{total:.3f} credits {'est. and actual' if mixed else 'actual'}"


def _g_register_row(record: dict) -> str:
    esc = functools.partial(html.escape, quote=True)
    task_id = _g_task_id(record)
    name = _g_str(record, "name", "(unnamed)")
    outcome = _g_str(record, "outcome", "unknown")
    date = _g_str(record, "date")
    actual = _g_num(record.get("actual"))
    estimate = _g_num(record.get("estimate"))
    requests = record.get("requests")
    if isinstance(requests, bool) or not isinstance(requests, int):
        requests = None
    minutes = _g_num(record.get("minutes"))
    basis = "actual" if actual is not None else "est."

    def num(v: Optional[float]) -> str:
        return "" if v is None else repr(v)

    text = " ".join([task_id, name, outcome, date, _g_str(record, "spec")]).lower()
    cls = ' class="killed"' if outcome != "finished" else ""
    return (
        f'<tr{cls} data-task_id="{esc(task_id)}" data-name="{esc(name.lower())}" '
        f'data-actual="{num(actual)}" data-estimate="{num(estimate)}" '
        f'data-requests="{"" if requests is None else requests}" data-minutes="{num(minutes)}" '
        f'data-outcome="{esc(outcome)}" data-date="{esc(date)}" data-text="{esc(text)}">'
        f"<td>{esc(task_id)}</td><td class=\"name\">{esc(name)}</td>"
        f"<td class=\"n\">{esc(_g_credits(actual) + ' actual' if actual is not None else 'n/a')}</td>"
        f"<td class=\"n\">{esc(_g_credits(estimate) + ' est.' if estimate is not None else 'n/a')}</td>"
        f"<td class=\"n\">{esc(f'{requests} {basis}' if requests is not None else 'n/a')}</td>"
        f"<td class=\"n\">{esc(f'{minutes:.1f} {basis}' if minutes is not None else 'n/a')}</td>"
        f"<td>{esc(_g_outcome_text(outcome))}</td><td>{esc(date)}</td></tr>"
    )


_GRAVEYARD_LEDE = ("Each stone is a task: a spec task Kiro finished, work that ticked no checkbox "
                   "(filed as unknown), or a task the breaker stopped. It is as tall as its price. "
                   "Open a stone for the estimate, the requests and the time.")

_GRAVEYARD_NO_RECEIPT_LABEL = "Early test stops, no receipt was kept"
_GRAVEYARD_TOTAL_NOTE = ("What Kiro charged to build this tool, and the live test runs "
                         "the breaker stopped.")

_GRAVEYARD_HILLS = (
    '<svg class="hills" viewBox="0 0 1600 220" preserveAspectRatio="none" aria-hidden="true">\n'
    '<path class="far" d="M0 120 C180 70 330 92 470 104 C640 118 760 46 930 58 '
    'C1110 70 1220 112 1380 96 C1480 86 1550 70 1600 76 L1600 220 L0 220 Z"/>\n'
    '<path class="near" d="M0 170 C140 140 300 150 460 160 C640 172 780 128 980 132 '
    'C1160 136 1300 168 1460 158 C1530 154 1570 148 1600 150 L1600 220 L0 220 Z"/>\n'
    "</svg>\n"
)


def _g_median(values: list) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


_GRAVEYARD_KICKER = "Tombstone, the record of its own build"
_GRAVEYARD_KICKER_LIVE = "Tombstone, the record of its own build and its live tests"


_GRAVEYARD_TAGLINE = "Tombstone stops a Kiro agent when a task goes over its budget."
_GRAVEYARD_LEGEND = "Height is the price. A crack means the breaker stopped it."
_GRAVEYARD_HINT = "Click a cracked stone to read its receipt."
_GRAVEYARD_PAST_TITLE = "The graveyard of this build"


def _g_header(rows: list, kicker: str = _GRAVEYARD_KICKER,
              video: Optional[str] = None, repo: Optional[str] = None) -> dict:
    """The scene header (task 13.1, 21.1): title, tagline, headline, three figures.

    Every figure is computed from the records. ``total`` is the sum of the
    cost each stone shows (an unpriced stone adds nothing). Costs print with
    two decimals and are a mix of actual and est. prices, as on the stones.
    The strip holds exactly three figures: tasks buried, stopped by the
    breaker and the most expensive task. The legend, the click hint (only
    with a stopped stone) and the optional ``video`` and ``repo`` links
    follow. Returns ``{"title": str, "html": str}``; ``html`` is already escaped.
    """
    esc = functools.partial(html.escape, quote=True)
    priced = [(c, r) for r in rows for c in [_g_cost(r)[0]] if c is not None]
    total = sum(c for c, _ in priced)
    title = f"The Graveyard, {total:.2f} credits"
    if rows:
        headline = f"{total:.2f} credits are buried here."
    else:
        headline = "Nothing is buried here yet."

    strip = ""
    n_stopped = sum(1 for r in rows if r.get("outcome") != "finished")
    if rows:
        top_cost = max((c for c, _ in priced), default=None)
        items = [
            ("", "tasks buried", str(len(rows))),
            (' class="red"' if n_stopped else "", "stopped by the breaker", str(n_stopped)),
            ("", "the most expensive task",
             "n/a" if top_cost is None else f"{top_cost:.2f}"),
        ]
        strip = '<dl class="proof">' + "".join(
            f"<div{cls}><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>"
            for cls, label, value in items) + "</dl>\n"
    legend = f'<p class="kicker legend">{esc(_GRAVEYARD_LEGEND)}'
    if n_stopped:
        legend += f" {esc(_GRAVEYARD_HINT)}"
    legend += "</p>\n"
    links = [(label, url) for label, url in (("Demo video", video), ("Repository", repo)) if url]
    link_html = ""
    if links:
        link_html = '<p class="kicker links">' + " ".join(
            f'<a href="{esc(url)}">{label}</a>' for label, url in links) + "</p>\n"

    total_note = (f'<p class="lede total-note">{esc(_GRAVEYARD_TOTAL_NOTE)}</p>\n'
                  if rows else "")
    markup = (
        '<section class="sky past" aria-labelledby="pasth">\n'
        '<div class="stars" aria-hidden="true"></div>\n<div class="intro">\n'
        f'<h2 id="pasth" class="past-title">{esc(_GRAVEYARD_PAST_TITLE)}</h2>\n'
        f'<p class="kicker">{esc(kicker)}</p>\n'
        f'<h3 class="total">{esc(headline)}</h3>\n'
        f'{total_note}'
        f'<p class="lede" id="lede">{esc(_GRAVEYARD_LEDE)}</p>\n'
        f"{strip}{legend}{link_html}</div>\n{_GRAVEYARD_HILLS}</section>\n"
    )
    return {"title": title, "html": markup}


# --- "Run the agent" block (R15) ------------------------------------------

_RUN_CAPTION = ("A recorded real Kiro CLI session, scrubbed. For every cap on the slider, "
                "the stop point was computed by Tombstone's own watcher code when this "
                "page was built.")

_RUN_CSS = """
.runner{position:relative;z-index:2;display:flow-root;max-width:880px;margin:0 auto;
  padding:28px 20px 96px}
.runner h2{font-size:26px;margin:0 0 6px}
.runner .caption{color:var(--muted);margin:14px 0 0;font-size:14px;max-width:60em}
.runner .controls{display:flex;flex-wrap:wrap;gap:14px 20px;align-items:center;margin:0 0 18px}
.runner label{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;font-size:16px}
.runner input[type=range]{width:min(320px,70vw);min-height:32px;accent-color:var(--ember)}
.runner button.run,.runner button.snd{font:inherit;font-size:17px;min-height:44px;min-width:96px;
  padding:6px 22px;color:var(--bone);background:#141b2a;border:1px solid rgba(236,231,218,.5);
  border-radius:4px;cursor:pointer}
.runner button.run:hover,.runner button.snd:hover{background:#1e2840}
.runner button.snd{font-size:15px;padding:6px 14px;color:var(--muted);border-color:rgba(236,231,218,.25)}
.runner button.snd[aria-pressed=true]{color:var(--bone)}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.runner .scrack{position:absolute;left:0;top:0;width:100%;height:100%;overflow:visible;
  pointer-events:none;opacity:0}
.runner .scrack path{fill:none;stroke:var(--ember);stroke-width:2;vector-effect:non-scaling-stroke;
  stroke-linejoin:round;stroke-dasharray:1;filter:drop-shadow(0 0 4px rgba(255,59,59,.9))}
.runner.stopped .scrack{opacity:.85}
.runner .scene{display:flex;flex-wrap:wrap;gap:20px 32px;align-items:flex-end}
.runner .meter{font-size:15px;color:var(--muted);min-width:180px}
.runner .meter b{display:block;font-size:34px;color:var(--bone);font-weight:600}
.runner .grave{flex:0 0 auto;display:flex;align-items:flex-end;min-height:260px;padding:0 20px 14px}
.runner .stone{--h:var(--rh);transition:height .5s ease,width .5s ease;cursor:default;
  background:var(--grain),linear-gradient(255deg,#9a9596,#6e6a6d 42%,#433f46);
  width:calc(var(--h) * .66);min-width:104px;align-items:center;justify-content:center;display:flex;color:#1d1a1c}
.runner .stone .cost{margin:0;color:#2a2528}
.runner.stopped .stone{box-shadow:0 0 34px 6px rgba(255,59,59,.35)}
.runner .verdict{flex:1 1 260px;min-width:0}
.runner .verdict .stop{font-size:30px;letter-spacing:.14em;color:var(--ember);font-weight:700;margin:0}
.runner .verdict p{margin:4px 0;overflow-wrap:anywhere}
.runner .verdict .refusal{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:14px;
  background:#1a1014;border-left:3px solid var(--ember);padding:8px 10px}
@media (prefers-reduced-motion:reduce){.runner .stone{transition:none}}
@media (max-width:640px){.runner{padding:20px 14px 96px}.runner .grave{min-height:200px;padding:0 6px 14px}
  .runner .meter b{font-size:28px}.runner .verdict .stop{font-size:24px}}
"""

_SCENE_CREDIT = "Scene and headstone images generated with Nano Banana Pro (Google Gemini)."

_HERO_JS = """
(function(){
var h=document.getElementById("hero"),p=h&&h.querySelector(".photo"),mm=window.matchMedia;
if(!p||!mm||!mm("(hover:hover) and (pointer:fine) and (min-width:768px)").matches||
   mm("(prefers-reduced-motion:reduce)").matches){return;}
var raf=0,x=0,y=0;
function set(){raf=0;p.style.transform="translate3d("+x.toFixed(1)+"px,"+y.toFixed(1)+"px,0)";}
h.addEventListener("pointermove",function(e){if(e.pointerType==="touch"){return;}
  var r=h.getBoundingClientRect();
  x=((e.clientX-r.left)/r.width-.5)*-24;y=((e.clientY-r.top)/r.height-.5)*-24;
  if(!raf){raf=requestAnimationFrame(set);}});
h.addEventListener("pointerleave",function(){x=0;y=0;if(!raf){raf=requestAnimationFrame(set);}});
})();
"""

_RUN_JS = """
(function(){
var root=document.getElementById("runner");
if(!root||typeof RUN_DATA==="undefined"){return;}
var T=RUN_DATA,rows=T.caps;
var slider=document.getElementById("run-cap"),out=document.getElementById("run-cap-out"),
    btn=document.getElementById("run-go"),count=document.getElementById("run-count"),
    stone=document.getElementById("run-stone"),cost=document.getElementById("run-stone-cost"),
    verdict=document.getElementById("run-verdict"),timer=null,
    hero=document.getElementById("hero")||root,snd=document.getElementById("run-sound"),
    ac=null,soundOn=false;
var reduce=window.matchMedia&&window.matchMedia("(prefers-reduced-motion:reduce)").matches;
function f(n){return n.toFixed(2);}
function row(){return rows[+slider.value];}
function height(c){return Math.round(70+150*Math.min(c/T.total,1));}
var last=0,earth=document.getElementById("run-earth");
function dig(){if(reduce||!earth){return;}earth.classList.remove("dig");void earth.offsetWidth;
  earth.classList.add("dig");}
function draw(c){count.textContent=f(c)+" credits est.";cost.textContent=f(c);
  stone.style.setProperty("--rh",height(c)+"px");if(c>last+1e-9){dig();}last=c;}
function clear(){verdict.textContent="";}
function line(cls,text){var p=document.createElement("p");if(cls){p.className=cls;}
  p.textContent=text;verdict.appendChild(p);}
function boom(){if(!soundOn||!ac){return;}
  try{var t=ac.currentTime,n=Math.floor(ac.sampleRate*.2),b=ac.createBuffer(1,n,ac.sampleRate),
    d=b.getChannelData(0),i,src=ac.createBufferSource(),hp=ac.createBiquadFilter(),
    g=ac.createGain(),o=ac.createOscillator(),og=ac.createGain();
  for(i=0;i<n;i++){d[i]=(Math.random()*2-1)*Math.pow(1-i/n,3);}
  src.buffer=b;hp.type="highpass";hp.frequency.value=1500;g.gain.value=.5;
  src.connect(hp);hp.connect(g);g.connect(ac.destination);src.start(t);
  o.type="sine";o.frequency.setValueAtTime(95,t+.05);
  o.frequency.exponentialRampToValueAtTime(38,t+.6);
  og.gain.setValueAtTime(.9,t+.05);og.gain.exponentialRampToValueAtTime(.001,t+.7);
  o.connect(og);og.connect(ac.destination);o.start(t+.05);o.stop(t+.75);}catch(e){}}
function reset(){if(timer){clearTimeout(timer);timer=null;}
  hero.classList.remove("stopped","scare");root.classList.remove("stopped");stone.className="hstone";count.classList.remove("hot");
  clear();draw(0);}
function finish(r){var steps=r.stop_at;
  if(steps){root.classList.add("stopped");hero.classList.add("stopped");
    if(!reduce){hero.classList.add("scare");boom();}stone.className="hstone crack-1";
    count.classList.add("hot");draw(r.figure);clear();line("stop","STOP");
    line("","hook exit code 2");line("refusal",r.refusal);
    line("","Without the breaker this session went on to "+f(T.total)+" credits est.");}
  else{draw(T.total);clear();line("","The cap was never reached. The session finished at "
    +f(T.total)+" credits est.");}}
function run(){reset();var r=row(),steps=r.stop_at||T.credits.length;
  if(reduce){finish(r);return;}
  var i=0,gap=6000/steps;
  (function tick(){i+=1;draw(T.credits[i-1]);
    if(i>=steps){timer=setTimeout(function(){finish(r);},gap);return;}
    timer=setTimeout(tick,gap);})();}
function label(){out.textContent=f(row().cap)+" credits";
  slider.setAttribute("aria-valuetext",f(row().cap)+" credits");}
var start=0;for(var k=0;k<rows.length;k++){if(rows[k].cap===T.default_cap){start=k;}}
slider.value=start;label();
slider.addEventListener("input",function(){label();reset();});
btn.addEventListener("click",run);
if(snd){snd.addEventListener("click",function(){soundOn=!soundOn;
  snd.setAttribute("aria-pressed",soundOn?"true":"false");
  snd.textContent=soundOn?"Sound on":"Sound off";
  if(soundOn&&!ac){try{var C=window.AudioContext||window.webkitAudioContext;
    if(C){ac=new C();}}catch(e){ac=null;}}
  if(ac&&ac.state==="suspended"){ac.resume();}});}
reset();
})();
"""


def _run_json(trace: dict) -> str:
    """Inline JSON for a script tag: ``<``, ``>``, ``&`` and line separators escaped."""
    text = json.dumps(trace, separators=(",", ":"), ensure_ascii=True)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _g_runner(trace: Optional[dict]) -> str:
    """The "Run the agent" block (R15.1, R15.3, R15.4). Empty when no trace.

    Server-rendered for the default cap so it reads with JavaScript off; the
    script only looks up the embedded results and animates them.
    """
    if not trace or not trace.get("caps"):
        return ""
    esc = functools.partial(html.escape, quote=True)
    rows = trace["caps"]
    default = next((i for i, r in enumerate(rows) if r["cap"] == trace["default_cap"]), 0)
    chosen = rows[default]
    total = trace["total"]
    if chosen["stop_at"]:
        result = (
            '<p class="stop">STOP</p><p>hook exit code 2</p>'
            f'<p class="refusal">{esc(chosen["refusal"])}</p>'
            f"<p>Without the breaker this session went on to {total:.2f} credits est.</p>")
        shown, state, cls = chosen["figure"], " stopped", "hstone crack-1"
    else:
        result = (f"<p>The cap was never reached. The session finished at "
                  f"{total:.2f} credits est.</p>")
        shown, state, cls = total, "", "hstone"
    height = round(70 + 150 * min(shown / total, 1)) if total else 70
    return (
        f'<section class="runner{state}" id="runner" aria-labelledby="runh">\n'
        '<h2 id="runh" class="sr">Run the agent</h2>\n'
        '<div class="controls"><label for="run-cap">Cap '
        f'<input id="run-cap" type="range" min="0" max="{len(rows) - 1}" step="1" '
        f'value="{default}" aria-label="Cap in credits"> '
        f'<output id="run-cap-out" for="run-cap">{chosen["cap"]:.2f} credits</output></label>'
        '<button id="run-go" type="button" class="run">Release the agent</button>'
        '<button id="run-sound" type="button" class="snd" aria-pressed="false">Sound off</button>'
        '</div>\n'
        '<div class="scene"><div class="meter">Spent so far'
        f'<b id="run-count" class="{"hot" if chosen["stop_at"] else ""}">'
        f"{shown:.2f} credits est.</b></div>"
        f'<div class="grave"><div class="earth" id="run-earth"><div id="run-stone" class="{cls}" '
        f'style="--rh:{height}px" role="img" '
        'aria-label="Headstone that rises with the spend">'
        f'<img class="hs" alt="" src="{_scene_uri("headstone.webp")}">'
        f'<span class="carve"><span class="cost" id="run-stone-cost">{shown:.2f}</span>'
        '<span class="cunit">credits est.</span></span>'
        '<svg class="scrack" viewBox="0 0 100 140" preserveAspectRatio="none" aria-hidden="true">'
        '<path class="glow" pathLength="1" d="M80 0 L73 26 L84 48 L71 74 L82 100 L75 140"/>'
        '<path class="cut" pathLength="1" d="M80 0 L73 26 L84 48 L71 74 L82 100 L75 140"/>'
        '</svg></div><div class="dirt" aria-hidden="true"></div></div></div>'
        f'<div class="verdict" id="run-verdict" role="status">{result}</div></div>\n'
        f'<p class="caption">{esc(_RUN_CAPTION)}</p>\n'
        "</section>\n"
    )




# --- the hero: a night graveyard scene around the breaker (R16) -------------

_HERO_CSS = """
/* hero (R16, R17): a photographed night cemetery, the breaker rising in the middle */
.hero{--k:1.3;position:relative;isolation:isolate;overflow:hidden;color:var(--bone);
  min-height:clamp(640px,100vh,900px);min-height:clamp(640px,100svh,900px);
  display:flex;flex-direction:column;justify-content:flex-end;background:#04060e}
.hero .photo{position:absolute;inset:-16px;z-index:0;pointer-events:none;
  background:#04060e var(--scene-d) 50% 100%/cover no-repeat;
  filter:saturate(.92) brightness(.96);transition:transform .3s ease-out;will-change:transform}
.hero .shade{position:absolute;inset:0;z-index:2;pointer-events:none;
  background:linear-gradient(180deg,rgba(3,5,12,.84) 0,rgba(3,5,12,.62) 22%,rgba(3,5,12,.2) 40%,rgba(3,5,12,0) 56%),
    radial-gradient(ellipse 125% 95% at 50% 62%,transparent 52%,rgba(2,3,8,.6) 100%),
    linear-gradient(0deg,rgba(3,5,10,.5),transparent 28%)}
.hero .fogl{position:absolute;left:0;width:200%;height:36%;bottom:var(--fb);pointer-events:none;
  opacity:var(--fo);transition:opacity .9s ease}
.hero .f1{--fb:30%;--fo:.42;z-index:1}
.hero .f2{--fb:14%;--fo:.5;z-index:3}
.hero .f3{--fb:2%;--fo:.42;z-index:3}
.hero.stopped .f1{--fo:.7}.hero.stopped .f2{--fo:.8}.hero.stopped .f3{--fo:.65}
.hero .burst{position:absolute;left:50%;bottom:6%;width:min(900px,120%);height:46%;
  transform:translateX(-50%);z-index:3;pointer-events:none;opacity:0;transition:opacity .9s ease;
  background:radial-gradient(ellipse 50% 50% at 50% 55%,rgba(205,212,226,.3),transparent 72%)}
.hero.stopped .burst{opacity:.8}
.hero::after{content:"";position:absolute;inset:0;z-index:4;pointer-events:none;
  background:#8a0f12;opacity:0}
.hero .toplinks{position:absolute;top:18px;right:max(18px,3vw);z-index:6;margin:0;font-size:14px;
  display:flex;gap:16px;letter-spacing:.03em}
.hero .toplinks a{color:var(--bone);opacity:.8;text-decoration:none;border-bottom:1px solid rgba(236,231,218,.35)}
.hero .toplinks a:hover{opacity:1}
.hero-inner{position:relative;z-index:5;width:100%;max-width:1240px;margin:0 auto;
  padding:clamp(56px,9vh,96px) max(18px,4vw) clamp(40px,5vw,90px);text-align:center}
.hero .tagline{margin:0 0 14px;font-size:clamp(13px,1.2vw,16px);letter-spacing:.2em;
  text-transform:uppercase;color:#c3ccdb;text-shadow:0 1px 14px #000}
.hero h1{font-weight:400;font-size:clamp(36px,6.2vw,92px);line-height:1.02;letter-spacing:-.02em;
  max-width:none;margin:0 0 clamp(14px,2.4vh,26px);text-wrap:balance;
  text-shadow:0 2px 28px #000,0 0 3px rgba(0,0,0,.8)}
.hero h1 em{color:#ff5a5a;font-style:italic;text-shadow:0 0 34px rgba(255,59,59,.35),0 2px 20px #000}
.hero .runner{max-width:none;margin:0;padding:0;text-align:left;position:relative;z-index:5}
.hero .runner .controls{justify-content:center;margin:0 0 clamp(8px,2vh,22px)}
.hero .runner label{color:#e4e8ef;text-shadow:0 1px 10px #000}
.hero .runner .scene{display:grid;grid-template-columns:1fr auto 1fr;gap:0 clamp(18px,3vw,48px);
  align-items:end}
.hero .runner .meter{justify-self:end;text-align:right;min-width:0;text-shadow:0 1px 12px #000}
.hero .runner .meter b{color:var(--bone)}
.hero .runner .grave{position:relative;display:block;min-height:0;padding:0}
.hero .runner .grave::before{content:"";position:absolute;left:-40%;right:-40%;bottom:-14px;height:70%;
  pointer-events:none;opacity:0;transition:opacity .9s ease;
  background:radial-gradient(ellipse 50% 50% at 50% 70%,rgba(255,59,59,.3),transparent 70%)}
.hero.stopped .runner .grave::before{opacity:1}
.hero .runner .verdict{flex:none;justify-self:start;max-width:34em;text-shadow:0 1px 12px #000}
.hero .runner .verdict .stop{text-shadow:0 0 28px rgba(255,59,59,.45),0 1px 10px #000}
.hero .runner .caption{text-align:center;margin:clamp(10px,2vh,22px) auto 0;color:#b4bdcc;
  text-shadow:0 1px 8px #000}
/* the rising headstone: its visible height is --rh on the R15 scale */
.hero .earth{position:relative;overflow:hidden;width:calc(var(--k) * 250px);height:calc(var(--k) * 228px);
  margin:0 auto}
.hero .hstone{position:absolute;left:50%;margin-left:calc(var(--k) * -73px);width:calc(var(--k) * 147px);
  height:calc(var(--k) * 220px);top:calc((228px - var(--rh)) * var(--k));
  transition:top .5s cubic-bezier(.3,.7,.3,1);cursor:default}
.hero .hstone .hs{display:block;width:100%;height:100%;user-select:none;-webkit-user-drag:none;
  filter:brightness(.92) contrast(1.08) saturate(.65) sepia(.4) hue-rotate(172deg)
    drop-shadow(0 0 10px rgba(140,168,222,.16));transition:filter .8s ease}
.hero.stopped .hstone .hs{filter:brightness(.78) contrast(1.12) saturate(.6) sepia(.4) hue-rotate(172deg)
    drop-shadow(0 0 12px rgba(255,59,59,.28))}
.hero .hstone .carve{position:absolute;left:14%;right:14%;top:21%;text-align:center;
  font-family:Georgia,"Times New Roman",serif;color:#070a10;pointer-events:none;
  text-shadow:0 1px 0 rgba(190,205,232,.4),0 -1px 0 rgba(0,0,0,.5)}
.hero .hstone .cost{display:block;margin:0;font-size:calc(var(--k) * 26px);line-height:1.05;
  font-weight:700;letter-spacing:.03em;color:#070a10}
.hero .hstone .cunit{display:block;font-size:calc(var(--k) * 11px);letter-spacing:.14em;
  text-transform:uppercase;opacity:.85}
.hero .hstone .scrack{left:14%;top:5%;width:72%;height:82%;overflow:visible}
.hero .runner.stopped .hstone .scrack{opacity:1}
.hero .hstone .scrack .glow{stroke:rgba(255,48,48,.55);stroke-width:7;filter:blur(2.5px)}
.hero .hstone .scrack .cut{stroke:#06080c;stroke-width:2.4;filter:none}
.hero .earth::after{content:"";position:absolute;left:0;right:0;bottom:0;height:calc(var(--k) * 26px);
  pointer-events:none;
  background:repeating-linear-gradient(97deg,transparent 0 4px,rgba(58,84,48,.95) 4px 5px,transparent 5px 8px,
      rgba(34,52,32,.95) 8px 9px,transparent 9px 12px) top/100% 9px no-repeat,
    linear-gradient(180deg,rgba(32,38,26,.0) 0,#1b2117 9px,#0b0e0a 100%);
  -webkit-mask-image:radial-gradient(ellipse 50% 125% at 50% 100%,#000 58%,transparent 100%);
  mask-image:radial-gradient(ellipse 50% 125% at 50% 100%,#000 58%,transparent 100%)}
.hero .dirt{position:absolute;left:14%;right:14%;bottom:calc(var(--k) * 16px);height:calc(var(--k) * 40px);
  pointer-events:none;opacity:0;
  background:radial-gradient(circle 2px at 14% 30%,#4a3b2c 90%,transparent),
    radial-gradient(circle 1.5px at 30% 10%,#2c2219 90%,transparent),
    radial-gradient(circle 2.5px at 48% 40%,#3a2d21 90%,transparent),
    radial-gradient(circle 1.5px at 66% 15%,#5a4836 90%,transparent),
    radial-gradient(circle 2px at 82% 35%,#2c2219 90%,transparent),
    radial-gradient(circle 1px at 92% 8%,#4a3b2c 90%,transparent)}
@media (prefers-reduced-motion:no-preference){
  .hero .fogl svg{animation:drift 90s linear infinite}
  .hero .f2 svg{animation-duration:62s;animation-direction:reverse}
  .hero .f3 svg{animation-duration:46s}
  @keyframes drift{from{transform:translateX(0)}to{transform:translateX(-50%)}}
  .hero.scare::after{animation:flash 1s ease-out both}
  .hero.scare .scrack path{animation:crackrun .5s ease-out both}
  .hero.scare .grave{animation:tremor .4s linear .05s both}
  .hero .earth.dig .hstone{animation:hshake .24s linear}
  .hero .earth.dig .dirt{animation:fall .7s ease-in both}
  @keyframes flash{0%{opacity:0}18%{opacity:.3}100%{opacity:0}}
  @keyframes crackrun{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
  @keyframes tremor{0%,100%{transform:none}20%{transform:translate(-3px,1px)}
    40%{transform:translate(3px,-1px)}60%{transform:translate(-2px,0)}80%{transform:translate(2px,1px)}}
  @keyframes hshake{0%,100%{transform:none}25%{transform:translate(-2px,1px)}
    50%{transform:translate(2px,-1px)}75%{transform:translate(-1px,0)}}
  @keyframes fall{0%{opacity:1;transform:translateY(-8px)}100%{opacity:0;transform:translateY(20px)}}
}
@media (prefers-reduced-motion:reduce){
  .hero .fogl,.hero .burst,.hero .hstone,.hero .hs,.hero .photo{transition:none}
  .hero::after{display:none}
  .hero .dirt{display:none}
}
@media (max-width:900px){
  .hero .runner .scene{grid-template-columns:1fr;justify-items:center;gap:6px}
  .hero .runner .meter{justify-self:center;text-align:center}
  .hero .runner .verdict{justify-self:center;text-align:center;max-width:30em}
  .hero .runner .verdict .refusal{text-align:left}
}
@media (max-width:767px){
  .hero .photo{background-image:var(--scene-m)}
}
@media (max-width:640px){
  .hero{--k:.74;min-height:640px}
  .hero .shade{background:linear-gradient(180deg,rgba(3,5,12,.86) 0,rgba(3,5,12,.66) 30%,rgba(3,5,12,.3) 58%,rgba(3,5,12,0) 78%),
    linear-gradient(0deg,rgba(3,5,10,.5),transparent 24%)}
  .hero-inner{padding:48px 16px 56px}
  .hero .toplinks{position:static;justify-content:flex-end;padding:12px 16px 0;position:relative;z-index:6}
  .hero .runner .controls{gap:8px 12px}
  .hero .runner input[type=range]{width:min(300px,76vw)}
}
.credit{margin:0;padding:10px 16px 18px;text-align:center;font-size:14px;color:#8e98a8;background:#05070c}
h3.total{font-weight:400;font-size:clamp(40px,5.6vw,96px);line-height:.98;letter-spacing:-.015em;
  margin:0 0 22px;max-width:11em;text-wrap:balance;text-shadow:0 2px 30px rgba(0,0,0,.5)}
.past-title{font-weight:400;font-size:clamp(26px,3vw,44px);margin:0 0 14px;letter-spacing:.01em;
  color:var(--bone)}
.sky.past{padding-top:clamp(48px,9vh,96px)}
"""


@functools.lru_cache(maxsize=None)
def _scene_uri(name: str) -> str:
    """One scene image from the package data as a base64 data URI ("" if missing)."""
    import base64
    from importlib import resources
    try:
        raw = resources.files("tombstone").joinpath("data", "scene", name).read_bytes()
    except (OSError, ModuleNotFoundError):
        return ""
    return "data:image/webp;base64," + base64.b64encode(raw).decode("ascii")


def _scene_css() -> str:
    """The two photographs as CSS custom properties (R17.1, R17.4)."""
    props = "".join(f'--{var}:url("{uri}");' for var, name in
                    (("scene-d", "scene_desktop.webp"), ("scene-m", "scene_mobile.webp"))
                    if (uri := _scene_uri(name)))
    return f".hero{{{props}}}" if props else ""


def _fog_svg(cls: str, blobs: list) -> str:
    """A fog band twice as wide as the hero; the second half repeats the first."""
    ells = ""
    for half in (0, 1600):
        for cx, cy, rx, ry in blobs:
            ells += (f'<ellipse cx="{cx + half}" cy="{cy}" rx="{rx}" ry="{ry}" '
                     'fill="url(#fogg)"/>')
    return (f'<div class="fogl {cls}" aria-hidden="true"><svg viewBox="0 0 3200 300" '
            'width="100%" height="100%" preserveAspectRatio="none">'
            f"{ells}</svg></div>")


def _g_scene() -> str:
    """The night photograph with a dark top, drifting fog layers and the frost glow."""
    fog_blobs_a = [(240, 170, 330, 70), (820, 200, 400, 62), (1340, 160, 300, 66)]
    fog_blobs_b = [(120, 190, 280, 58), (640, 150, 360, 72), (1150, 190, 340, 60), (1500, 170, 220, 54)]
    fog = (
        '<svg width="0" height="0" aria-hidden="true" focusable="false" style="position:absolute">'
        '<defs><radialGradient id="fogg" cx=".5" cy=".5" r=".5">'
        '<stop offset="0" stop-color="#c3cde0" stop-opacity=".5"/>'
        '<stop offset=".6" stop-color="#a5b2cc" stop-opacity=".16"/>'
        '<stop offset="1" stop-color="#8fa0c0" stop-opacity="0"/></radialGradient></defs></svg>'
    )
    return ('<div class="photo" aria-hidden="true"></div>\n'
            f'{fog}{_fog_svg("f1", fog_blobs_a)}<div class="shade" aria-hidden="true"></div>'
            f'{_fog_svg("f2", fog_blobs_b)}{_fog_svg("f3", fog_blobs_a[::-1])}'
            '<div class="burst" aria-hidden="true"></div>')


def _g_hero(trace: Optional[dict], video: Optional[str] = None,
            repo: Optional[str] = None) -> str:
    """The first screen (R16.1, R16.2, R16.4, R16.5): scene, headline, controls.

    The R15 runner sits in the middle. The page is server-rendered in the
    stopped state for the default cap, so with JavaScript off the hero shows
    the 0.75 result as text and a cracked central headstone.
    """
    esc = functools.partial(html.escape, quote=True)
    runner = _g_runner(trace)
    state = " stopped" if 'class="runner stopped"' in runner else ""
    links = [(label, url) for label, url in (("Demo video", video), ("Repository", repo)) if url]
    nav = ""
    if links:
        nav = '<p class="toplinks">' + " ".join(
            f'<a href="{esc(url)}">{label}</a>' for label, url in links) + "</p>\n"
    noscript = ""
    if runner:
        noscript = ("<noscript><style>.hero .runner .controls{display:none}</style>"
                    '<p class="caption">JavaScript is off. The scene shows the recorded result '
                    "for a cap of 0.75 credits.</p></noscript>\n")
    return (
        f'<header class="hero{state}" id="hero">\n{_g_scene()}\n{nav}'
        '<div class="hero-inner">\n'
        f'<p class="tagline">{esc(_GRAVEYARD_TAGLINE)}</p>\n'
        '<h1>Set a cap. <em>Release the agent.</em></h1>\n'
        f"{runner}{noscript}</div>\n</header>\n"
    )


def render_graveyard(records: list, plots: Optional[dict] = None,
                     included: Optional[dict] = None,
                     receipts: Optional[dict] = None,
                     video: Optional[str] = None, repo: Optional[str] = None) -> str:
    """Render tombstone records as one static HTML page (R2.6, R2.7, R4.4).

    Headstones, plots, the stopped row, unmarked graves and the register are
    plain server-rendered HTML, so they read with JavaScript off. The inline
    script only opens the detail card and sorts and filters the register.
    ``plots`` maps a top-level task number (as text) to its name from
    ``tasks.md``; a missing number reads "Task N". No external resource; the
    SVG data URI keeps its xmlns. Every record field is HTML escaped.

    ``included`` maps a record id to the file name it was read from with
    ``--include`` (R2.9). When any stopped record is in it, the stopped row
    note names the file and the kicker mentions the live tests.

    ``receipts`` maps a record id to its parsed autopsy (R2.11). A stopped
    record without one shows "No receipt was kept for this stop." in the card.

    ``video`` and ``repo`` are optional URLs drawn as two plain links in the
    header (R2.12). Each is escaped and left out when not given.
    """
    esc = functools.partial(html.escape, quote=True)
    from . import demo
    try:
        run_trace = demo.trace()
    except Exception:  # noqa: BLE001 - the page never fails for the demo block
        run_trace = None
    receipts = receipts if isinstance(receipts, dict) else {}
    plots = plots if isinstance(plots, dict) else {}
    rows = [r for r in (records or []) if isinstance(r, dict)]
    costs = [_g_cost(r)[0] or 0.0 for r in rows]
    peak = max(costs + [1.0])

    stopped = [r for r in rows if r.get("outcome") != "finished"]
    finished = [r for r in rows if r.get("outcome") == "finished"]
    known = [r for r in finished if _g_task_id(r) != "unknown"]
    unmarked = [r for r in finished if _g_task_id(r) == "unknown"]

    included = included if isinstance(included, dict) else {}
    live = [r for r in stopped if r.get("id") in included]
    live_note = ""
    if live:
        files = []
        for r in live:
            name = str(included[r.get("id")])
            if name not in files:
                files.append(name)
        kept = esc(", ".join(files))
        if len(live) == 1 and len(stopped) == 1:
            live_note = f" It is a live test run on a scratch project, kept in {kept}."
        else:
            lead = f"All {len(live)}" if len(live) == len(stopped) else f"{len(live)} of them"
            live_note = f" {lead} are live test runs on scratch projects, kept in {kept}."
    on_page = any(r.get("id") in included for r in rows)

    stop_nos = _g_stop_numbers(stopped)
    with_receipt, without_receipt = _g_stop_order(stopped, receipts)
    ordered = with_receipt + without_receipt
    shapes = _g_row_shapes(ordered, peak, "stopped")
    stones = [_g_stone(r, peak, i, "stopped", sh, receipts.get(r.get("id")), stop_nos.get(id(r)))
              for i, (r, sh) in enumerate(zip(ordered, shapes))]
    if without_receipt:
        stones.insert(len(with_receipt),
                      f'<p class="row-label">{esc(_GRAVEYARD_NO_RECEIPT_LABEL)}</p>')
    parts: list[str] = []
    if stopped:
        parts.append(
            '<div class="section-note wide stopped-note"><h2>Stopped by the breaker</h2>'
            "These tasks went over their cap or were blocked before they started. "
            "The next tool call was refused and the agent stopped. "
            f"The cost is what Kiro charged before it did.{live_note}</div>"
            '<div class="plot stopped wide"><div class="row" style="justify-content:flex-start">'
            + "".join(stones)
            + "</div></div>"
            '<div class="section-note wide"><h2>Finished tasks</h2>'
            "Grouped by the top-level task in tasks.md.</div>"
        )

    groups: dict[str, list] = {}
    for r in known:
        groups.setdefault(_g_plot_key(_g_task_id(r)), []).append(r)
    for key in sorted(groups, key=lambda k: _g_task_sort(k)):
        members = sorted(groups[key], key=lambda r: _g_task_sort(_g_task_id(r)))
        title = plots.get(key) if isinstance(plots.get(key), str) and plots.get(key) else f"Task {key}"
        n = len(members)
        parts.append(
            '<div class="plot"><div class="row">'
            + "".join(_g_stone(r, peak, i, key, sh) for i, (r, sh)
                      in enumerate(zip(members, _g_row_shapes(members, peak, key))))
            + f'</div><div class="plaque"><span class="num">{esc(key)}</span><b>{esc(title)}</b>'
            f'<span class="sum">{n} {"task" if n == 1 else "tasks"}, '
            f"{esc(_g_sum_label(members))}</span></div></div>"
        )

    if unmarked:
        parts.append(
            '<div class="section-note wide"><h2>Unmarked tasks</h2>'
            f"{len(unmarked)} tasks ticked no box in tasks.md: bug fixes, retries, a hello. "
            "Tombstone does not guess their task, so each is named by the first words of its "
            f"prompt. {esc(_g_sum_label(unmarked))}.</div>"
            '<div class="plot unmarked wide"><div class="row" style="flex-wrap:wrap;row-gap:40px">'
            + "".join(_g_stone(r, peak, i, "unmarked", sh) for i, (r, sh)
                      in enumerate(zip(unmarked, _g_row_shapes(unmarked, peak, "unmarked"))))
            + "</div></div>"
        )

    if not rows:
        parts.append('<p class="section-note wide">No tombstones yet. It is quiet here.</p>')

    register_rows = sorted(
        rows, key=lambda r: (_g_num(r.get("actual")) is None, -(_g_num(r.get("actual")) or 0.0)))
    header = _g_header(rows, _GRAVEYARD_KICKER_LIVE if on_page else _GRAVEYARD_KICKER,
                       video, repo)

    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(header['title'])}</title>\n"
        f"<style>\n{_GRAVEYARD_CSS}\n{_RUN_CSS}\n{_HERO_CSS}\n{_scene_css()}\n</style>\n</head>\n<body>\n"
        '<div class="fog" aria-hidden="true"></div>\n'
        f"{_g_hero(run_trace, video, repo)}"
        f"{header['html']}"
        "<main>\n"
        f'<section class="ground" id="ground" aria-label="Tasks by plot">'
        f'<div class="field">{"".join(parts)}</div></section>\n'
        '<section class="register" aria-labelledby="regh">\n'
        '<h2 id="regh">The register</h2>\n'
        "<p>Every row of tombstones.json, the same data as the stones above.</p>\n"
        '<div class="controls"><label>Find a task '
        '<input id="q" type="search" placeholder="task id or words"></label></div>\n'
        '<div class="tablewrap"><table>\n<thead><tr>'
        '<th><button type="button" data-k="task_id">Task</button></th>'
        '<th><button type="button" data-k="name">Name</button></th>'
        '<th class="n"><button type="button" data-k="actual">Actual</button></th>'
        '<th class="n"><button type="button" data-k="estimate">Estimate</button></th>'
        '<th class="n"><button type="button" data-k="requests">Requests</button></th>'
        '<th class="n"><button type="button" data-k="minutes">Minutes</button></th>'
        '<th><button type="button" data-k="outcome">Outcome</button></th>'
        '<th><button type="button" data-k="date">Date</button></th>'
        "</tr></thead>\n"
        f'<tbody id="tb">{"".join(_g_register_row(r) for r in register_rows)}</tbody>'
        "</table></div>\n</section>\n</main>\n"
        "<footer>Every number on this page comes from tombstones.json, written by "
        "Tombstone's hooks while Kiro worked. Credits are Kiro credits as the session "
        "files report them.</footer>\n"
        f'<p class="credit">{esc(_SCENE_CREDIT)}</p>\n'
        '<dialog id="card" aria-labelledby="ct"><h3 id="ct"></h3><p class="meta" id="cm"></p>'
        '<dl id="cd"></dl><p class="prompt" id="cp" hidden></p><p class="gap" id="cg"></p>'
        '<section id="cr" class="receipt" aria-labelledby="crh" hidden><h4 id="crh">Receipt</h4>'
        '<p id="crn">No receipt was kept for this stop.</p>'
        '<div id="crb" hidden><dl id="crd"></dl><pre id="crt" tabindex="0"></pre>'
        '<p class="rl">Last tool calls</p><ol id="crc"></ol></div></section>'
        '<form method="dialog"><button>Close</button></form></dialog>\n'
        f"<script>\n{_GRAVEYARD_JS}\n"
        f"{('var RUN_DATA=' + _run_json(run_trace) + ';' + _RUN_JS) if run_trace else ''}\n{_HERO_JS}"
        "\n</script>\n"
        f"</body>\n</html>\n"
    )


_PLOT_LINE = re.compile(r"^- \[[ xX\-~]\] (\d+)\. (.+?)\s*$")


def read_plot_names(project: Path) -> dict:
    """Plot names from the top-level ``- [ ] N. Name`` lines of every
    ``.kiro/specs/*/tasks.md`` (checked or not, backticks removed).

    The first spec in name order wins on a repeated number. Unreadable or
    symlinked files are skipped; never raises.
    """
    names: dict[str, str] = {}
    specs = Path(project) / ".kiro" / "specs"
    try:
        files = sorted(specs.glob("*/tasks.md"))
    except OSError:
        return names
    for path in files:
        try:
            if path.is_symlink():
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            match = _PLOT_LINE.match(line)
            if match:
                title = match.group(2).replace("`", "").strip()
                if title:
                    names.setdefault(match.group(1), title)
    return names


class IncludeError(Exception):
    """An ``--include`` file is missing, unreadable or not a tombstones file."""


def load_includes(paths: list) -> list:
    """Read each ``--include`` file as a tombstones file (R2.9).

    Uses the same reader as ``tombstones.json`` but strictly: a missing,
    unreadable or malformed file raises ``IncludeError`` with a plain message.
    Returns ``[(file name, [records])]`` in the order given.
    """
    loaded = []
    for raw in paths or []:
        path = Path(raw).expanduser()
        if not path.is_file():
            raise IncludeError(f"include file not found: {path}")
        value, valid = _read_json(path)
        if not valid or value is None or not isinstance(value.get("tombstones"), list):
            raise IncludeError(f"include file is not a tombstones file: {path}")
        loaded.append((path.name, [r for r in value["tombstones"] if isinstance(r, dict)]))
    return loaded


def merge_includes(records: list, loaded: list) -> tuple:
    """Merge included records by id; the project record wins (R2.9).

    Returns ``(records, included)`` where ``included`` maps the id of each
    added record to the name of the file it came from.
    """
    merged = list(records)
    seen = {r.get("id") for r in merged if isinstance(r, dict)}
    included: dict = {}
    for name, items in loaded:
        for record in items:
            rid = record.get("id")
            if rid in seen:
                continue
            seen.add(rid)
            merged.append(record)
            included[rid] = name
    return merged, included


def _collect_receipts(project: Path, records: list, extra_dirs: Optional[list]) -> dict:
    """Map each stopped record id to its autopsy, when one matches (R2.11)."""
    from . import autopsy

    stopped = [r for r in records if isinstance(r, dict) and r.get("outcome") != "finished"]
    if not stopped:
        return {}
    dirs = []
    own = project / TOMBSTONE_DIR / "autopsy"
    if own.is_dir():
        dirs.append(own)
    dirs.extend(extra_dirs or [])
    found = autopsy.read_autopsies(dirs)
    out: dict = {}
    for record in stopped:
        match = autopsy.match_autopsy(record, found)
        if match is not None:
            out[record.get("id")] = match
    return out


def write_graveyard(project: Path, out: Optional[Path] = None,
                    includes: Optional[list] = None,
                    autopsy_dirs: Optional[list] = None,
                    video: Optional[str] = None, repo: Optional[str] = None) -> Path:
    """Render ``tombstones.json`` of ``project`` and write the page.

    Default target is ``.tombstone/graveyard.html``. Reads the store
    without modifying it. ``includes`` is the result of ``load_includes``.
    Returns the path written.
    """
    project = Path(project)
    store = load_tombstones(project / "tombstones.json")
    records, included = merge_includes(store.get("tombstones") or [], includes or [])
    receipts = _collect_receipts(project, records, autopsy_dirs)
    page = render_graveyard(records, read_plot_names(project), included, receipts,
                            video, repo)
    target = Path(out).expanduser() if out is not None \
        else project / TOMBSTONE_DIR / "graveyard.html"
    if out is None and not guard(project, target):
        # Symlinked sidecar or page: nothing is written.
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(target, page)
    return target
