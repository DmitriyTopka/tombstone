"""Load tombstone.toml and decide whether a task may start.

Task 4.1 covers configuration loading and the similar-task math:

- ``load_config`` reads ``tombstone.toml`` with ``tomllib``. A missing file
  gives the defaults from R3.2. A parse error or a wrong type for one key
  falls back to that key's default and records a warning in ``state.json``;
  it never blocks the agent (R3.1, R3.2).
- ``task_type`` classifies a task name into one of the five types by a
  first-match keyword lookup (R3.5).
- ``keywords`` is the set of words of length 3 or more in a name minus a
  small stop list.
- ``similar`` finds finished records of the same task type that share at
  least one keyword, and ``median_similar`` returns the median cost, the
  count and the basis (``actual`` or ``est.``) (R3.5, R3.6).

Later tasks (4.2) add the gate decision and the pre-tool hook, and task 9.1
adds the cached ``/usage`` plan-balance probe. Those are not part of 4.1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Literal, Optional


# --- configuration (R3.1, R3.2) --------------------------------------------


# Defaults from R3.2 plus the two keys R3.1 lists that R3.2 leaves implicit
# (minimum plan balance 0 disables the check; it is stated in design.md).
DEFAULT_TASK_CAP = 40.0
DEFAULT_SPEC_CAP = 300.0
DEFAULT_QUIET_WINDOW_MINUTES = 10.0
DEFAULT_AVG_REQUEST_PRICE = 0.045
DEFAULT_MIN_PLAN_BALANCE = 0.0
DEFAULT_KILL_GRACE_SECONDS = 30.0


@dataclass(frozen=True)
class Config:
    """The caps and budget settings, with every field defaulted (R3.2).

    ``warnings`` carries one human-readable line per key that fell back to its
    default because the file had a bad value. The caller records them in
    ``state.json``; a bad config never blocks the agent (R3.1).
    """
    task_cap: float = DEFAULT_TASK_CAP
    spec_cap: float = DEFAULT_SPEC_CAP
    quiet_window_minutes: float = DEFAULT_QUIET_WINDOW_MINUTES
    avg_request_price: float = DEFAULT_AVG_REQUEST_PRICE
    min_plan_balance: float = DEFAULT_MIN_PLAN_BALANCE
    kill_grace_seconds: float = DEFAULT_KILL_GRACE_SECONDS
    warnings: tuple[str, ...] = ()


# Each config field, its [table][key] in tombstone.toml and its default.
_CONFIG_KEYS: tuple[tuple[str, str, str, float], ...] = (
    ("task_cap", "caps", "task", DEFAULT_TASK_CAP),
    ("spec_cap", "caps", "spec", DEFAULT_SPEC_CAP),
    ("quiet_window_minutes", "stall", "quiet_window_minutes",
     DEFAULT_QUIET_WINDOW_MINUTES),
    ("avg_request_price", "budget", "avg_request_price",
     DEFAULT_AVG_REQUEST_PRICE),
    ("min_plan_balance", "budget", "min_plan_balance", DEFAULT_MIN_PLAN_BALANCE),
    ("kill_grace_seconds", "stop", "kill_grace_seconds",
     DEFAULT_KILL_GRACE_SECONDS),
)


def _coerce_number(value: object) -> Optional[float]:
    """Return ``value`` as a float when it is a real number, else None.

    A ``bool`` is rejected: ``True`` is an ``int`` in Python but is never a
    valid cap or price. A string is rejected too; the TOML type for these
    keys is a number.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def load_config(project: Path) -> Config:
    """Load ``tombstone.toml`` from the project root (R3.1, R3.2).

    A missing file returns the defaults with no warning. A file that cannot be
    parsed at all returns the defaults with one warning. For a parsed file
    each known key is read; a missing or wrongly typed key falls back to its
    default and adds a warning naming the key. The function never raises and
    never blocks the agent.
    """
    import tomllib

    path = Path(project) / "tombstone.toml"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Config()
    except OSError as error:
        return Config(warnings=(f"tombstone.toml unreadable ({error}), using defaults",))

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        return Config(warnings=(f"tombstone.toml parse error ({error}), using defaults",))

    values: dict[str, float] = {}
    warnings: list[str] = []

    known: dict[str, set[str]] = {}
    for _field, table, key, _default in _CONFIG_KEYS:
        known.setdefault(table, set()).add(key)
    for table_name, section in data.items():
        if table_name not in known:
            warnings.append(
                f"unknown table [{table_name}] in tombstone.toml, ignored "
                f"(known tables: {', '.join(sorted(known))})"
            )
        elif isinstance(section, dict):
            for key_name in section:
                if key_name not in known[table_name]:
                    warnings.append(
                        f"unknown key {table_name}.{key_name} in tombstone.toml, "
                        f"ignored (known: {', '.join(sorted(known[table_name]))})"
                    )

    for field, table, key, default in _CONFIG_KEYS:
        section = data.get(table)
        present = isinstance(section, dict) and key in section
        number = _coerce_number(section.get(key)) if present else None
        if present and number is None:
            warnings.append(
                f"{table}.{key} in tombstone.toml is not a number, using default {default}"
            )
        values[field] = number if number is not None else default

    return Config(warnings=tuple(warnings), **values)


# --- task type and keywords (R3.5) -----------------------------------------


TaskType = Literal["test", "migration", "refactor", "docs", "other"]

# First-match keyword lookup on the lowercased name (design: task_type).
# Order matters: the first type whose any keyword is a substring wins.
_TYPE_KEYWORDS: tuple[tuple[TaskType, tuple[str, ...]], ...] = (
    ("test", ("test", "pytest", "spec")),
    ("migration", ("migrat", "schema")),
    ("refactor", ("refactor", "rename", "cleanup")),
    ("docs", ("doc", "readme")),
)


def task_type(name: str) -> TaskType:
    """Classify a task name into one of the five types (R3.5).

    First-match substring lookup on the lowercased name. ``test``, ``pytest``
    or ``spec`` give ``test``; ``migrat`` or ``schema`` give ``migration``;
    ``refactor``, ``rename`` or ``cleanup`` give ``refactor``; ``doc`` or
    ``readme`` give ``docs``; anything else is ``other``.
    """
    lowered = (name or "").lower()
    for type_name, needles in _TYPE_KEYWORDS:
        if any(needle in lowered for needle in needles):
            return type_name
    return "other"


# Words that carry no signal for similarity. Kept short on purpose.
_STOP_WORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "over",
    "per", "its", "add", "use", "run", "when", "then", "than", "but", "not",
    "are", "was", "has", "have", "all", "any", "one", "two", "new", "old",
})

_WORD = re.compile(r"[a-z0-9]+")


def keywords(name: str) -> set[str]:
    """The set of signal words in a task name (R3.5).

    Words are the alphanumeric runs of the lowercased name of length 3 or
    more, minus a small stop list. The result drives the similarity match.
    """
    words = _WORD.findall((name or "").lower())
    return {word for word in words if len(word) >= 3 and word not in _STOP_WORDS}


# --- similar tasks and median cost (R3.5, R3.6) ----------------------------


def _record_cost(record: dict) -> tuple[Optional[float], str]:
    """The cost of a record and its basis: ``actual`` if present, else ``est.``.

    Returns ``(value, basis)``. A record with neither an actual nor an
    estimate yields ``(None, "est.")`` and is skipped by the callers.
    """
    actual = record.get("actual")
    if isinstance(actual, (int, float)):
        return float(actual), "actual"
    estimate = record.get("estimate")
    if isinstance(estimate, (int, float)):
        return float(estimate), "est."
    return None, "est."


def similar(
    records: list[dict],
    name: str,
    spec: Optional[str] = None,
) -> list[dict]:
    """Finished records similar to a task name by type and keywords (R3.5).

    A record is similar when its outcome is ``finished``, its task type equals
    the type of ``name``, and its keyword set shares at least one word with
    the keywords of ``name``. When ``spec`` is given, only records of that
    spec are considered. The input order is preserved.
    """
    target_type = task_type(name)
    target_keywords = keywords(name)
    if not target_keywords:
        return []

    matches: list[dict] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        if record.get("outcome") != "finished":
            continue
        if spec is not None and record.get("spec") != spec:
            continue
        record_name = record.get("name") or ""
        record_type = record.get("task_type")
        # Recompute the type from the name when the record did not store one,
        # so older records without the field still match.
        if not isinstance(record_type, str):
            record_type = task_type(record_name)
        if record_type != target_type:
            continue
        if keywords(record_name) & target_keywords:
            matches.append(record)
    return matches


@dataclass(frozen=True)
class Similar:
    """The median cost of similar tasks, with how it was derived (R3.6).

    ``value`` is None when no similar task exists; the caller then uses the
    task cap as the reference. ``count`` is the number of similar records.
    ``basis`` is ``actual`` when every contributing record had an actual cost,
    ``est.`` when any fell back to the estimate, and ``est.`` when there is no
    similar task at all.
    """
    value: Optional[float]
    count: int
    basis: Literal["actual", "est."]


def median_similar(
    records: list[dict],
    name: str,
    spec: Optional[str] = None,
) -> Similar:
    """Median cost of similar tasks (R3.5, R3.6).

    Costs are ``actual`` when present, else ``estimate``. The basis is
    ``actual`` only when every similar record contributed an actual cost;
    otherwise it is ``est.``. With no similar record the value is None and the
    caller falls back to the task cap (R3.6).
    """
    matches = similar(records, name, spec=spec)
    costs: list[float] = []
    bases: list[str] = []
    for record in matches:
        value, basis = _record_cost(record)
        if value is None:
            continue
        costs.append(value)
        bases.append(basis)

    if not costs:
        return Similar(value=None, count=0, basis="est.")

    overall_basis: Literal["actual", "est."] = (
        "actual" if all(basis == "actual" for basis in bases) else "est."
    )
    return Similar(value=round(median(costs), 4), count=len(costs), basis=overall_basis)


# --- gate decision (R3.3, R3.4, R3.6) --------------------------------------


@dataclass(frozen=True)
class Decision:
    """The outcome of the task-start gate.

    ``allow`` is True when the tool call may run. ``new_task`` is True when
    the task differs from ``state.current_task``; the caller then stores it as
    the current task on an allow. On a block, ``message`` is the one stderr
    line and ``outcome`` is ``cap``.
    """
    allow: bool
    new_task: bool = False
    message: str = ""
    outcome: Optional[str] = None


def _task_field(task: object, name: str) -> Optional[str]:
    value = getattr(task, name, None)
    return str(value) if value is not None else None


def _is_current(state: dict, task: object) -> bool:
    """True when ``task`` is already the task stored as current in state."""
    current = state.get("current_task") if isinstance(state, dict) else None
    if not isinstance(current, dict):
        return False
    return (
        str(current.get("task_id")) == _task_field(task, "task_id")
        and (current.get("spec") or None) == (getattr(task, "spec", None) or None)
    )


def spec_spent(records: list[dict], spec: Optional[str],
               open_estimate: float = 0.0) -> tuple[float, str]:
    """Credits spent on a spec and the basis of that figure (R3.3).

    Sums the cost of every record of the spec (``actual``, else ``estimate``)
    and adds the open-turn estimate. ``cap`` records cost nothing and are
    skipped. The basis is ``actual`` only when every counted figure is actual
    and there is no open-turn estimate, otherwise ``est.``.
    """
    total = 0.0
    basis = "actual"
    for record in records or []:
        if not isinstance(record, dict) or record.get("spec") != spec:
            continue
        if record.get("outcome") == "cap":
            continue
        value, record_basis = _record_cost(record)
        if value is None:
            continue
        total += value
        if record_basis != "actual":
            basis = "est."
    if open_estimate > 0:
        total += open_estimate
        basis = "est."
    return total, basis


def _gate_message(task: object, spec: Optional[str], sim: Similar,
                  config: Config, remaining: float, spent_basis: str,
                  headline: str, advice: str) -> str:
    """Build the one-line stderr message, every number labeled (R4.4)."""
    task_id = _task_field(task, "task_id") or "unknown"
    name = _task_field(task, "name")
    label = f'task {task_id} "{name}"' if name else f"task {task_id}"
    if sim.value is not None:
        similar_part = (f"Similar tasks cost {sim.value:.3f} "
                        f"({sim.basis}, median of {sim.count}).")
    else:
        similar_part = (f"No similar task, using task cap "
                        f"{config.task_cap:.3f} (est. reference).")
    spec_part = (f'Spec "{spec}" has {max(remaining, 0.0):.3f} of '
                 f"{config.spec_cap:.3f} left ({spent_basis}).")
    return f"TOMBSTONE: {label} blocked. {headline} {similar_part} {spec_part} {advice}"


def check_task_start(
    config: Config,
    records: list[dict],
    state: dict,
    task: object,
    balance: Optional[float] = None,
) -> Decision:
    """Decide whether a task may start (R3.3, R3.4, R3.6).

    ``task`` is any object with ``task_id``, ``name`` and ``spec`` (a
    ``TaskRef``). A task is new when it differs from ``state.current_task``;
    a task that is already current is always allowed. For a new task:

    1. spent = the spec's record costs plus the open-turn estimate, and
       remaining = spec cap minus spent;
    2. spent at or over the spec cap blocks (R3.4);
    3. remaining below the reference blocks (R3.3). The reference is the
       median of similar tasks, or the task cap when none exist (R3.6);
    4. a known plan balance below ``min_plan_balance`` blocks.

    Pure: it reads its arguments only. ``balance`` is the cached plan balance
    or None when unknown.
    """
    if _is_current(state, task):
        return Decision(allow=True, new_task=False)

    spec = getattr(task, "spec", None)
    turn = state.get("turn") if isinstance(state, dict) else None
    open_estimate = 0.0
    if isinstance(turn, dict) and isinstance(turn.get("estimate"), (int, float)):
        open_estimate = float(turn["estimate"])

    spent, spent_basis = spec_spent(records, spec, open_estimate)
    remaining = config.spec_cap - spent
    name = _task_field(task, "name") or ""
    sim = median_similar(records, name, spec=None)
    reference = sim.value if sim.value is not None else config.task_cap

    if spent >= config.spec_cap:
        message = _gate_message(
            task, spec, sim, config, remaining, spent_basis,
            f"Spec cap reached ({spent:.3f} {spent_basis} spent).",
            "Raise caps.spec in tombstone.toml to continue.",
        )
        return Decision(False, True, message, "cap")

    if remaining < reference:
        message = _gate_message(
            task, spec, sim, config, remaining, spent_basis,
            f"Remaining budget is below the reference {reference:.3f}.",
            "Raise caps.spec in tombstone.toml to continue.",
        )
        return Decision(False, True, message, "cap")

    if (config.min_plan_balance > 0 and balance is not None
            and balance < config.min_plan_balance):
        message = _gate_message(
            task, spec, sim, config, remaining, spent_basis,
            f"Plan balance {balance:.3f} (actual) is below "
            f"budget.min_plan_balance {config.min_plan_balance:.3f}.",
            "Lower budget.min_plan_balance in tombstone.toml to continue.",
        )
        return Decision(False, True, message, "cap")

    return Decision(allow=True, new_task=True)


# --- pre-tool hook, cap path (R3.3, R3.4, R11.5) ---------------------------


def gate_task(project: Path, stdin: dict, state: dict):
    """The task a tool call is about to work on, or None.

    The STDIN task id wins. Otherwise it is the first unchecked task of the
    active spec in file order. Returns a ``TaskRef`` or None when there is
    no active spec or nothing left to do.
    """
    from . import tombstones

    spec = tombstones.active_spec(project, state)
    if spec is None:
        return None
    tasks = tombstones.parse_tasks(tombstones._tasks_file(project, spec))

    stdin_id = tombstones._stdin_task_id(stdin)
    if stdin_id is not None:
        name = next((t.name for t in tasks if t.task_id == stdin_id), None)
        return tombstones.TaskRef(spec=spec, task_id=stdin_id, name=name,
                                  source="stdin")
    for task in tasks:
        if not task.checked:
            return tombstones.TaskRef(spec=spec, task_id=task.task_id,
                                      name=task.name, source="first-unchecked")
    return None


def cached_balance(project: Path) -> Optional[float]:
    """The plan balance from ``usage.json``, or None. Reads only, never probes."""
    import json

    try:
        data = json.loads((Path(project) / ".kiro" / "tombstone" / "usage.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("balance") if isinstance(data, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _cap_record(task: object, today: str) -> dict:
    """A ``cap`` record: no turns, zero cost (design: Records)."""
    name = _task_field(task, "name")
    spec = getattr(task, "spec", None)
    task_id = _task_field(task, "task_id") or "unknown"
    return {
        "id": f"{spec or 'unknown'}/{task_id}",
        "task_id": task_id,
        "name": name,
        "spec": spec,
        "task_type": task_type(name or ""),
        "engine": None,
        "model": None,
        "requests": 0,
        "estimate": 0.0,
        "actual": 0.0,
        "minutes": 0.0,
        "outcome": "cap",
        "false_positive": None,
        "date": today,
        "turns": [],
    }


def run_gate(project: Path, stdin: dict) -> Optional[str]:
    """Run the cap gate for one tool call. Returns the stderr line or None.

    Fast path: when the task is already current this reads state and
    tasks.md and returns without loading the config or touching any file.
    On an allowed new task it stores ``state.current_task``. On a block it
    writes one ``cap`` record per task id and returns the message.
    """
    import json
    from datetime import date

    from . import tombstones

    project = Path(project)
    state_path = project / tombstones.TOMBSTONE_DIR / "state.json"
    state, valid = tombstones._read_json(state_path)
    state = state if valid and state is not None else {}

    task = gate_task(project, stdin, state)
    if task is None or _is_current(state, task):
        return None

    config = load_config(project)
    store_path = project / "tombstones.json"
    records = tombstones.load_tombstones(store_path).get("tombstones") or []
    decision = check_task_start(config, records, state, task,
                                balance=cached_balance(project))

    if decision.allow:
        def mark(value: dict) -> dict:
            value["current_task"] = {"spec": task.spec, "task_id": task.task_id}
            if config.warnings:
                value["warnings"] = list(config.warnings)
            return value

        tombstones.update_json(state_path, mark, default={"version": 1})
        return None

    record = _cap_record(task, date.today().isoformat())

    def add_once(store: dict) -> dict:
        existing = store.get("tombstones")
        if isinstance(existing, list) and any(
            isinstance(item, dict) and item.get("id") == record["id"]
            for item in existing
        ):
            return store
        return tombstones.upsert_record(store, json.loads(json.dumps(record)))

    tombstones.update_json(
        store_path, add_once, default=tombstones._empty_store(),
        pending_apply=tombstones.apply_pending_record,
    )
    return decision.message
