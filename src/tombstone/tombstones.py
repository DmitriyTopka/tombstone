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
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

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


def _sidecar_dir(path: Path) -> Path:
    """Create and return the directory for lock, pending and log files."""
    directory = _tombstone_dir(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _log_warning(sidecar: Path, message: str) -> None:
    """Append one warning line to ``errors.log``. Never raises."""
    stamp = datetime.now(timezone.utc).isoformat()
    try:
        with (sidecar / "errors.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
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
    """Write ``value`` as pretty JSON atomically (temp file, fsync, replace)."""
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(directory)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Leave no temp file behind on failure.
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


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
        with (sidecar / "pending.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except (OSError, TypeError, ValueError):
        pass


def _drain_pending(sidecar: Path, name: str) -> list[dict]:
    """Read and clear the pending changes queued for ``name``.

    Returns the list of queued ``change`` objects in order. Lines for other
    targets are preserved. A missing or unreadable queue yields an empty list.
    """
    queue = sidecar / "pending.jsonl"
    try:
        text = queue.read_text(encoding="utf-8")
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
                queue.write_text("\n".join(kept) + "\n", encoding="utf-8")
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
    sidecar = _sidecar_dir(path)
    lock_path = sidecar / f"{path.name}.lock"

    with open(lock_path, "w") as lock_handle:
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


def resolve_task(project: Path, stdin: dict, state: dict) -> TaskRef:
    """Resolve the task a turn belongs to, first match wins (R2.3, R2.4).

    1. A task id carried in the hook STDIN.
    2. The checkbox diff: ids the post-tool hook recorded in
       ``state.turn.checked`` for this turn. When more than one was checked,
       the first by id is returned; the share split across them is applied by
       the record builder in task 3.3.
    3. The first unchecked task of the active spec.
    4. ``unknown`` with the current turn id (R2.4).
    """
    project = Path(project)
    spec = active_spec(project, state)

    stdin_id = _stdin_task_id(stdin)
    if stdin_id is not None:
        return TaskRef(
            spec=spec,
            task_id=stdin_id,
            name=_name_for_task(project, spec, stdin_id),
            source="stdin",
        )

    checked = _checked_this_turn(state)
    if checked:
        task_id = sorted(checked)[0]
        return TaskRef(
            spec=spec,
            task_id=task_id,
            name=_name_for_task(project, spec, task_id),
            source="checkbox-diff",
        )

    if spec is not None:
        for task in parse_tasks(_tasks_file(project, spec)):
            if not task.checked:
                return TaskRef(
                    spec=spec,
                    task_id=task.task_id,
                    name=task.name,
                    source="first-unchecked",
                )

    turn = state.get("turn") if isinstance(state, dict) else None
    turn_id = turn.get("turn_id") if isinstance(turn, dict) else None
    return TaskRef(
        spec=spec,
        task_id="unknown",
        name=None,
        source="fallback",
        turn_id=str(turn_id) if turn_id else None,
    )


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
            if checked and not old_map.get(task_id, False):
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
        # A snapshot exists: diff the current file against it.
        newly_checked = [
            task_id
            for task_id, checked in current.items()
            if checked and not previous.get(task_id, False)
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
DEFAULT_AVG_REQUEST_PRICE = 0.045


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

    ref = resolve_task(project, stdin if isinstance(stdin, dict) else {}, state)

    # Split the turn across every task checked this turn (design: share).
    checked = _checked_this_turn(state)
    share = 1.0 / len(checked) if len(checked) > 1 else 1.0

    avg_price = _avg_price(state, DEFAULT_AVG_REQUEST_PRICE)
    estimate = None
    if isinstance(turn.requests, int):
        estimate = turn.requests * avg_price

    record_id = _record_id(ref)
    existing = _existing_record(store, record_id)

    outcome = str(stdin.get("outcome")) if isinstance(stdin, dict) \
        and isinstance(stdin.get("outcome"), str) else "finished"

    record = build_record(
        ref,
        turn,
        engine=session.engine,
        model=turn.model or session.model,
        estimate=estimate,
        share=share,
        outcome=outcome,
        existing=existing,
    )

    update_json(
        project / "tombstones.json",
        lambda store_value: upsert_record(store_value, record),
        default=_empty_store(),
        pending_apply=apply_pending_record,
    )

    if turn.state == "closed" and turn.cost is not None \
            and isinstance(turn.requests, int):
        # The cost was already on disk: fold the priced turn into the window.
        update_json(
            state_path,
            lambda s: update_price_window(s, turn.requests, turn.cost),
            default={"version": 1},
        )
    elif turn.cost is None:
        # The cost has not landed yet. Start a detached reconcile child that
        # polls the session and fills actual once the usage_summary appears
        # (Bug 1). The Stop hook itself returns at once.
        spawn_reconcile(project, session.session_id, turn.turn_id,
                        kiro_home=kiro_home)

    return record


def spawn_reconcile(project: Path, session_id: Optional[str],
                    turn_id: Optional[str],
                    kiro_home: Optional[Path] = None) -> None:
    """Start a detached ``tombstone hook reconcile`` child (Bug 1).

    The child polls the session every 0.5 s for up to 30 s and fills ``actual``
    and ``requests`` once the ``usage_summary`` lands, using the same locked
    update. It is fully detached (``start_new_session=True``, stdout and stderr
    to devnull) so the Stop hook returns in well under 200 ms. Never raises; a
    failure to spawn is logged by the caller's try/except.
    """
    import subprocess
    import sys

    args = [
        sys.executable, "-m", "tombstone", "hook", "reconcile",
        "--session", session_id or "",
        "--turn", turn_id or "latest",
        "--project", str(project),
    ]
    if kiro_home is not None:
        args += ["--kiro-home", str(kiro_home)]

    try:
        devnull = open(os.devnull, "wb")
    except OSError:
        devnull = None
    try:
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=devnull if devnull is not None else subprocess.DEVNULL,
            stderr=devnull if devnull is not None else subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        # The child holds its own copy of the fd; closing ours is safe.
        if devnull is not None:
            try:
                devnull.close()
            except OSError:
                pass


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
            record = _apply_reconciled_turn(project, turn)
            if turn.cost is not None and isinstance(turn.requests, int):
                update_json(
                    state_path,
                    lambda s: update_price_window(s, turn.requests, turn.cost),
                    default={"version": 1},
                )
            return record

        if clock() >= deadline:
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
        records = store.get("tombstones")
        if not isinstance(records, list):
            return store
        for record in records:
            if not isinstance(record, dict):
                continue
            entries = record.get("turns")
            if not isinstance(entries, list):
                continue
            for pos, entry in enumerate(entries):
                if not isinstance(entry, dict) or entry.get("turn_id") != turn.turn_id:
                    continue
                estimate = entry.get("estimate")
                share = entry.get("share", 1.0)
                entries[pos] = turn_entry(turn, estimate, share)
                _recompute_record_totals(record)
                updated.clear()
                updated.update(record)
                return store
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
        sidecar.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        line = (f"{stamp} reconcile timed out for session "
                f"{session_id or 'unknown'} turn {turn_id}, actual left null\n")
        with (sidecar / "errors.log").open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass
