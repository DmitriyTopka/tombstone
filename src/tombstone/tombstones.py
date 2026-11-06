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
import html
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .safepath import (
    append_nofollow,
    guard,
    is_safe_target,
    open_nofollow,
    write_atomic,
)

# Directory that holds the lock files, pending queues and the error log.
TOMBSTONE_DIR = Path(".kiro") / "tombstone"

TOMBSTONES_VERSION = 1


def _tombstone_dir(path: Path) -> Path:
    """Return the ``.kiro/tombstone`` directory for a target file.

    ``tombstones.json`` lives at the repo root, while ``state.json`` and the
    usage cache live in ``.kiro/tombstone``. The lock, pending and log files
    all live in ``.kiro/tombstone`` next to the state so they stay out of the
    committed data. We walk up from the target until we find a ``.kiro``
    directory, falling back to the target's own parent.
    """
    path = Path(path)
    if path.parent.name == "tombstone" and path.parent.parent.name == ".kiro":
        return path.parent
    for parent in path.parents:
        candidate = parent / TOMBSTONE_DIR
        if candidate.is_dir():
            return candidate
    # No project marker yet: place sidecar files beside the target.
    return path.parent


def _project_of_sidecar(sidecar: Path) -> Path:
    """The project root for a sidecar directory (its grandparent when it is
    ``.kiro/tombstone``, else the directory itself)."""
    sidecar = Path(sidecar)
    if sidecar.name == "tombstone" and sidecar.parent.name == ".kiro":
        return sidecar.parent.parent
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
    ``.kiro/tombstone``, reads the current value (or ``default`` when the file
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
    project = (sidecar.parent.parent if sidecar.name == "tombstone"
               and sidecar.parent.name == ".kiro" else path.parent)
    lock_path = sidecar / f"{path.name}.lock"
    if not (guard(project, sidecar) and guard(project, path)
            and guard(project, lock_path)):
        # Symlinked .kiro or state path: write nothing, report the intent.
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


# Child log inside .kiro/tombstone/. One line per start, fill and timeout.
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

# Outcomes the page knows how to badge. Anything else is shown as written.
_OUTCOME_LABELS = {
    "finished": "FINISHED",
    "killed": "KILLED",
    "cap": "BLOCKED BY CAP",
}

_GRAVEYARD_CSS = """
:root{--sky-top:#05060a;--sky-bottom:#141a26;--stone:#3a3f47;--stone-light:#4b515b;--stone-dark:#24282e;
--ink:#d9dde3;--muted:#8a919c;--moss:#5f7f5a;--red:#ff3b3b;--red-glow:rgba(255,59,59,.55);--amber:#d9a441}
*{box-sizing:border-box}
html,body{margin:0;background:var(--sky-top);color:var(--ink);font-family:ui-serif,Georgia,"Times New Roman",serif}
.sky{min-height:100vh;background:linear-gradient(var(--sky-top),var(--sky-bottom));padding:28px 16px 48px;position:relative;overflow:hidden}
.moon{position:absolute;top:30px;right:8%;width:76px;height:76px;border-radius:50%;
background:radial-gradient(circle at 35% 35%,#f4f1e6,#c9c4b0 70%);box-shadow:0 0 60px 12px rgba(240,236,220,.16)}
header,main,footer{position:relative;max-width:1080px;margin:0 auto}
h1{font-size:clamp(26px,4vw,40px);letter-spacing:.04em;margin:0 0 6px;font-weight:600}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace}
.sub{color:var(--muted);font-size:13px}
.stats{display:flex;flex-wrap:wrap;gap:22px;margin-top:14px;font-size:13px}
.stats b{display:block;color:var(--ink);font-size:18px}
.stats span{color:var(--muted)}
.controls{margin-top:16px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input[type=search]{background:#1b2029;color:var(--ink);border:1px solid #343b47;border-radius:6px;padding:8px 12px;font:13px ui-monospace,Menlo,monospace;min-width:220px}
button{background:#1b2029;color:var(--ink);border:1px solid #343b47;border-radius:6px;padding:8px 12px;font:13px ui-monospace,Menlo,monospace;cursor:pointer}
button:hover,button:focus-visible,input:focus-visible{border-color:var(--red);outline:none}
button[aria-pressed=true]{border-color:var(--moss)}
.yard{margin-top:28px;display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:22px 16px}
.grave{display:flex;flex-direction:column;align-items:center}
.grave[hidden]{display:none}
.stone{width:100%;border-radius:70px 70px 6px 6px;padding:22px 12px 14px;text-align:center;
background:linear-gradient(160deg,var(--stone-light),var(--stone) 45%,var(--stone-dark));
box-shadow:inset -6px -8px 14px rgba(0,0,0,.35),0 10px 18px rgba(0,0,0,.6);border-bottom:4px solid var(--moss)}
.rip{font-size:11px;letter-spacing:.3em;color:var(--muted)}
.tid{font-size:12px;color:var(--muted);margin-top:2px;word-break:break-all}
.cost{font-size:clamp(28px,3.4vw,38px);font-weight:700;margin:8px 0 2px;font-variant-numeric:tabular-nums}
.unit{font-size:11px;color:var(--muted)}
.name{font-size:13px;line-height:1.3;color:#c3c9d3;margin-top:8px;min-height:34px;word-break:break-word}
.meta{font-size:11px;color:var(--muted);margin-top:6px;line-height:1.5}
.badge{display:inline-block;margin-top:8px;padding:2px 8px;border-radius:10px;font:10px ui-monospace,Menlo,monospace;letter-spacing:.12em;border:1px solid var(--moss);color:var(--moss)}
.grave.killed .stone{border-bottom-color:var(--red)}
.grave.killed .cost{color:var(--red);text-shadow:0 0 12px var(--red-glow)}
.grave.killed .badge{border-color:var(--red);color:var(--red)}
.grave.cap .stone{border-bottom-color:var(--amber)}
.grave.cap .badge{border-color:var(--amber);color:var(--amber)}
.mound{width:112%;height:14px;margin-top:-3px;border-radius:50%;background:#1c2a22;opacity:.8}
.empty{color:var(--muted);text-align:center;padding:60px 0;font-size:15px}
footer{margin-top:36px;color:var(--muted);font-size:11px}
""".strip()

_GRAVEYARD_JS = """
(function(){
var yard=document.getElementById("yard");
if(!yard){return;}
var cards=Array.prototype.slice.call(yard.querySelectorAll(".grave"));
var box=document.getElementById("filter");
var buttons=Array.prototype.slice.call(document.querySelectorAll("button[data-sort]"));
var state={key:"cost",dir:-1};
function value(card,key){
  var raw=card.getAttribute("data-"+key)||"";
  if(key==="name"||key==="date"||key==="outcome"){return raw;}
  var n=parseFloat(raw);
  return isNaN(n)?-1:n;
}
function sortNow(){
  cards.sort(function(a,b){
    var x=value(a,state.key),y=value(b,state.key);
    if(x<y){return -1*state.dir;}
    if(x>y){return 1*state.dir;}
    return 0;
  });
  cards.forEach(function(card){yard.appendChild(card);});
  buttons.forEach(function(btn){
    var on=btn.getAttribute("data-sort")===state.key;
    btn.setAttribute("aria-pressed",on?"true":"false");
    btn.textContent=btn.getAttribute("data-label")+(on?(state.dir<0?" (desc)":" (asc)"):"");
  });
}
function filterNow(){
  var q=(box&&box.value||"").toLowerCase().trim();
  cards.forEach(function(card){
    var hay=card.getAttribute("data-text")||"";
    card.hidden=q!==""&&hay.indexOf(q)===-1;
  });
}
buttons.forEach(function(btn){
  btn.addEventListener("click",function(){
    var key=btn.getAttribute("data-sort");
    if(state.key===key){state.dir=-state.dir;}else{state.key=key;state.dir=(key==="name")?1:-1;}
    sortNow();
  });
});
if(box){box.addEventListener("input",filterNow);}
sortNow();
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
    """The price shown big on a card and its label (R4.4).

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


def _g_median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _g_card(record: dict) -> str:
    """One tombstone card as HTML. Every field is escaped."""
    esc = html.escape
    cost, label = _g_cost(record)
    outcome = record.get("outcome") if isinstance(record.get("outcome"), str) else "unknown"
    badge = _OUTCOME_LABELS.get(outcome, outcome.upper())
    task_id = str(record.get("task_id") if record.get("task_id") is not None else "unknown")
    name = record.get("name") if isinstance(record.get("name"), str) and record.get("name") else "(unnamed)"
    spec = record.get("spec") if isinstance(record.get("spec"), str) else "-"
    engine = record.get("engine") if isinstance(record.get("engine"), str) else "-"
    model = record.get("model") if isinstance(record.get("model"), str) else "unknown model"
    date = record.get("date") if isinstance(record.get("date"), str) else ""

    estimate = _g_num(record.get("estimate"))
    actual = _g_num(record.get("actual"))
    requests = record.get("requests") if isinstance(record.get("requests"), int) \
        and not isinstance(record.get("requests"), bool) else None
    minutes = _g_num(record.get("minutes"))

    # Requests and minutes are measured once the turn is priced, else est.
    basis = "actual" if actual is not None else "est."
    meta = [f"est. {_g_credits(estimate)}"]
    meta.append(f"actual {_g_credits(actual)}" if actual is not None else "actual pending")
    meta.append(f"{requests if requests is not None else 'n/a'} req ({basis})")
    meta.append(f"{minutes:.1f} min ({basis})" if minutes is not None else "n/a min")

    false_positive = record.get("false_positive")
    extra = ""
    if false_positive is True:
        extra = '<div class="meta mono">marked as false positive</div>'
    elif false_positive is False and outcome == "killed":
        extra = '<div class="meta mono">confirmed real stop</div>'

    css = {"killed": "killed", "cap": "cap"}.get(outcome, "")
    text = " ".join([task_id, name, spec, engine, model, outcome, date]).lower()
    sort_cost = cost if cost is not None else -1
    return (
        f'<article class="grave {css}" data-cost="{sort_cost}" '
        f'data-date="{esc(date, quote=True)}" '
        f'data-requests="{requests if requests is not None else -1}" '
        f'data-name="{esc(name.lower(), quote=True)}" '
        f'data-outcome="{esc(outcome, quote=True)}" '
        f'data-text="{esc(text, quote=True)}">'
        f'<div class="stone">'
        f'<div class="rip">R.I.P.</div>'
        f'<div class="tid mono">{esc(spec)} / task {esc(task_id)}</div>'
        f'<div class="cost">{esc(_g_credits(cost))}</div>'
        f'<div class="unit mono">credits {esc(label)}</div>'
        f'<div class="name">{esc(name)}</div>'
        f'<div class="meta mono">{esc(" | ".join(meta))}</div>'
        f'<div class="meta mono">{esc(engine)} | {esc(model)} | {esc(date or "no date")}</div>'
        f'{extra}'
        f'<span class="badge">{esc(badge)}</span>'
        f'</div><div class="mound"></div></article>'
    )


def render_graveyard(records: list) -> str:
    """Render tombstone records as one static HTML page (R2.6, R4.4).

    Inline CSS and JS only: no link, no external script, no font, no network
    address. One card per record, the price as the big number, sorted by cost
    (highest first) before the page loads, so it reads without JS. The JS adds
    sorting and a filter box. Every record field is HTML escaped. Bad input
    (non-dict items, missing fields) is skipped or shown as ``n/a``.
    """
    rows = [r for r in (records or []) if isinstance(r, dict)]
    rows.sort(key=lambda r: (_g_cost(r)[0] is None, -(_g_cost(r)[0] or 0.0)))

    actuals = [v for v in (_g_num(r.get("actual")) for r in rows) if v is not None]
    est_only = sum(1 for r in rows if _g_num(r.get("actual")) is None
                   and _g_num(r.get("estimate")) is not None)
    est_only_total = sum(_g_num(r.get("estimate")) or 0.0 for r in rows
                         if _g_num(r.get("actual")) is None)
    killed = sum(1 for r in rows if r.get("outcome") == "killed")
    capped = sum(1 for r in rows if r.get("outcome") == "cap")
    median = _g_median(actuals)

    stats = [
        (str(len(rows)), "tombstones"),
        (_g_credits(sum(actuals)), f"credits actual ({len(actuals)} priced)"),
        (_g_credits(median), "median per tombstone (actual)"),
        (f"{_g_credits(est_only_total)}", f"credits est. ({est_only} not priced yet)"),
        (str(killed), "killed by the breaker"),
        (str(capped), "blocked by a cap"),
    ]
    stats_html = "".join(
        f"<div><b>{html.escape(value)}</b><span>{html.escape(label)}</span></div>"
        for value, label in stats
    )

    if rows:
        body = "\n".join(_g_card(r) for r in rows)
    else:
        body = '<p class="empty">No tombstones yet. The graveyard is quiet.</p>'

    buttons = "".join(
        f'<button type="button" data-sort="{key}" data-label="{label}" '
        f'aria-pressed="false">{label}</button>'
        for key, label in (("cost", "Cost"), ("date", "Date"),
                           ("requests", "Requests"), ("name", "Name"),
                           ("outcome", "Outcome"))
    )

    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Tombstone Graveyard</title>\n"
        f"<style>\n{_GRAVEYARD_CSS}\n</style>\n</head>\n<body>\n"
        '<div class="sky">\n<div class="moon"></div>\n<header>\n'
        "<h1>The Graveyard</h1>\n"
        '<div class="sub mono">tombstones.json, every task priced in Kiro credits. '
        "Numbers say est. or actual.</div>\n"
        f'<div class="stats mono">{stats_html}</div>\n'
        '<div class="controls">'
        '<input id="filter" type="search" placeholder="Filter by name, task, spec, outcome" '
        'aria-label="Filter tombstones">'
        f"{buttons}</div>\n</header>\n"
        f'<main class="yard" id="yard">\n{body}\n</main>\n'
        '<footer class="mono">Generated from tombstones.json. '
        "No network, no external files.</footer>\n</div>\n"
        f"<script>\n{_GRAVEYARD_JS}\n</script>\n</body>\n</html>\n"
    )


def write_graveyard(project: Path, out: Optional[Path] = None) -> Path:
    """Render ``tombstones.json`` of ``project`` and write the page.

    Default target is ``.kiro/tombstone/graveyard.html``. Reads the store
    without modifying it. Returns the path written.
    """
    project = Path(project)
    store = load_tombstones(project / "tombstones.json")
    page = render_graveyard(store.get("tombstones") or [])
    target = Path(out).expanduser() if out is not None \
        else project / TOMBSTONE_DIR / "graveyard.html"
    if out is None and not guard(project, target):
        # Symlinked sidecar or page: nothing is written.
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(target, page)
    return target
