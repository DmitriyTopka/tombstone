"""Read V2 and V3 Kiro session files and price turns.

One parser, format detected per session, read only, never raises on bad data.
Task 1.1 implements V3 and the shared scaffolding. V2 lands in task 1.2.
Task 1.3 adds session discovery (``find_session``) and open-turn progress
(``open_turn_progress``) for both engines.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, NamedTuple, Optional

Engine = Literal["v2", "v3"]


@dataclass(frozen=True)
class Turn:
    engine: Engine
    session_id: str
    turn_id: str
    model: Optional[str]
    requests: Optional[int]        # None when unknown
    cost: Optional[float]          # credits, None when unpriced
    start: Optional[datetime]
    end: Optional[datetime]        # None while the turn is open
    state: Literal["closed", "open"]
    unpriced_reason: Optional[str] = None   # set when cost is None on a closed turn


@dataclass(frozen=True)
class Session:
    engine: Optional[Engine]
    session_id: str
    path: Path
    cwd: Optional[str]
    model: Optional[str]
    turns: list[Turn] = field(default_factory=list)
    unknown_reason: Optional[str] = None


class Progress(NamedTuple):
    """Live progress of the running turn (R4.1, R5.1).

    ``requests_so_far`` counts requests seen since the open turn started,
    with the same rule the parser uses for an open turn. ``size_bytes`` and
    ``mtime`` are of the growing jsonl file and drive the pulse check.
    """
    requests_so_far: int
    size_bytes: int
    mtime: float


def _parse_time(value: object) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp, tolerating a trailing Z. None on failure."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _v3_root(path: Path) -> Optional[Path]:
    """Return the sess_* directory for a V3 path, or None."""
    if path.is_dir() and path.name.startswith("sess_"):
        return path
    if path.is_file() and path.name == "messages.jsonl":
        parent = path.parent
        if parent.name.startswith("sess_"):
            return parent
    return None


def detect_format(path: Path) -> Optional[Engine]:
    """Detect the session format from a path (R10.1).

    V3: a ``sess_*`` directory (or its ``messages.jsonl``) that has both
    ``session.json`` and ``messages.jsonl``.
    V2: a ``<uuid>.json`` file (or its ``.jsonl`` sibling) whose JSON has
    ``session_state.conversation_metadata``.
    Anything else returns None.
    """
    root = _v3_root(path)
    if root is not None:
        if (root / "session.json").is_file() and (root / "messages.jsonl").is_file():
            return "v3"
        return None

    json_path: Optional[Path] = None
    if path.is_file() and path.suffix == ".json":
        json_path = path
    elif path.is_file() and path.suffix == ".jsonl":
        sibling = path.with_suffix(".json")
        if sibling.is_file():
            json_path = sibling
    if json_path is not None:
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        state = data.get("session_state") if isinstance(data, dict) else None
        if isinstance(state, dict) and "conversation_metadata" in state:
            return "v2"
    return None


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, skipping blank lines and lines that fail to parse.

    A half-written last line is simply dropped (R1.5). Never raises.
    """
    events: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return events
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def _load_session_json(root: Path) -> dict:
    try:
        data = json.loads((root / "session.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _v3_cost(summary: dict) -> tuple[Optional[float], Optional[str]]:
    """Cost from a usage_summary payload (R1.2).

    Sum ``usage`` over promptTurnSummaries entries with unit ``credit``.
    If the summary has no credit entry, return an unpriced reason.
    """
    summaries = summary.get("promptTurnSummaries")
    if not isinstance(summaries, list):
        return None, "no credit entry in usage_summary"
    total = 0.0
    found = False
    for entry in summaries:
        if not isinstance(entry, dict):
            continue
        if entry.get("unit") == "credit":
            usage = entry.get("usage")
            if isinstance(usage, (int, float)):
                total += float(usage)
                found = True
    if not found:
        return None, "no credit entry in usage_summary"
    return total, None


def _parse_v3(root: Path, path: Path) -> Session:
    data = _load_session_json(root)
    session_id = data.get("id") or root.name
    model = data.get("modelId")
    workspace = data.get("workspacePaths") or data.get("rootPaths")
    cwd = workspace[0] if isinstance(workspace, list) and workspace else None

    events = _read_jsonl(root / "messages.jsonl")

    # Group by turnId, keeping document order per turn.
    starts: dict[str, dict] = {}
    summaries: dict[str, dict] = {}
    assistant_counts: dict[str, int] = {}
    order: list[str] = []

    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        ptype = payload.get("type")
        turn_id = payload.get("turnId")
        if ptype == "turn_start" and isinstance(turn_id, str):
            if turn_id not in starts:
                order.append(turn_id)
            starts[turn_id] = event
        elif ptype == "usage_summary" and isinstance(turn_id, str):
            if turn_id not in starts and turn_id not in summaries:
                order.append(turn_id)
            summaries[turn_id] = event
        elif ptype == "assistant" and isinstance(turn_id, str):
            if turn_id not in starts and turn_id not in summaries \
                    and turn_id not in assistant_counts:
                order.append(turn_id)
            assistant_counts[turn_id] = assistant_counts.get(turn_id, 0) + 1

    turns: list[Turn] = []
    for turn_id in order:
        start_event = starts.get(turn_id)
        summary_event = summaries.get(turn_id)
        start = _parse_time(start_event.get("timestamp")) if start_event else None

        if summary_event is not None:
            payload = summary_event.get("payload", {})
            end = _parse_time(summary_event.get("timestamp"))
            request_ids = payload.get("requestIds")
            requests = len(request_ids) if isinstance(request_ids, list) else None
            cost, reason = _v3_cost(payload)
            turns.append(Turn(
                engine="v3",
                session_id=session_id,
                turn_id=turn_id,
                model=model,
                requests=requests,
                cost=cost,
                start=start,
                end=end,
                state="closed",
                unpriced_reason=reason,
            ))
        else:
            # Open turn: count assistant events since turn_start (R1.3, HYPOTHESIS).
            requests = assistant_counts.get(turn_id, 0)
            turns.append(Turn(
                engine="v3",
                session_id=session_id,
                turn_id=turn_id,
                model=model,
                requests=requests,
                cost=None,
                start=start,
                end=None,
                state="open",
                unpriced_reason=None,
            ))

    return Session(
        engine="v3",
        session_id=session_id,
        path=path,
        cwd=cwd,
        model=model,
        turns=turns,
    )


def _v2_json_path(path: Path) -> Optional[Path]:
    """Return the <uuid>.json file for a V2 path, or None.

    Accepts the ``.json`` itself or its ``.jsonl`` sibling.
    """
    if path.is_file() and path.suffix == ".json":
        return path
    if path.is_file() and path.suffix == ".jsonl":
        sibling = path.with_suffix(".json")
        if sibling.is_file():
            return sibling
    return None


def _v2_cost(metering: object) -> tuple[Optional[float], Optional[str]]:
    """Cost from a V2 ``metering_usage`` list (R1.1).

    Sum ``value`` over entries whose ``unit`` is ``credit``. An empty list,
    or a list with no credit entry, gives an unpriced turn.
    """
    if not isinstance(metering, list) or not metering:
        return None, "empty metering_usage"
    total = 0.0
    found = False
    for entry in metering:
        if not isinstance(entry, dict):
            continue
        if entry.get("unit") == "credit":
            value = entry.get("value")
            if isinstance(value, (int, float)):
                total += float(value)
                found = True
    if not found:
        return None, "no credit entry in metering_usage"
    return total, None


def _v2_duration(duration: object) -> Optional[timedelta]:
    """Turn duration from a ``{secs, nanos}`` object. None on bad data."""
    if not isinstance(duration, dict):
        return None
    secs = duration.get("secs")
    nanos = duration.get("nanos", 0)
    if not isinstance(secs, (int, float)):
        return None
    if not isinstance(nanos, (int, float)):
        nanos = 0
    return timedelta(seconds=float(secs), microseconds=float(nanos) / 1000.0)


def _parse_v2(json_path: Path, path: Path) -> Session:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}

    session_id = data.get("session_id") or json_path.stem
    cwd = data.get("cwd")
    state = data.get("session_state")
    cm = state.get("conversation_metadata") if isinstance(state, dict) else None
    metadatas = cm.get("user_turn_metadatas") if isinstance(cm, dict) else None

    # Session-level model: the model of the first turn that names one.
    session_model: Optional[str] = None
    turns: list[Turn] = []

    if isinstance(metadatas, list):
        for index, meta in enumerate(metadatas):
            if not isinstance(meta, dict):
                continue
            model = meta.get("model")
            if isinstance(model, str) and session_model is None:
                session_model = model

            total = meta.get("total_request_count")
            requests = int(total) if isinstance(total, int) else None

            cost, reason = _v2_cost(meta.get("metering_usage"))

            end = _parse_time(meta.get("end_timestamp"))
            delta = _v2_duration(meta.get("turn_duration"))
            start = end - delta if (end is not None and delta is not None) else None

            turns.append(Turn(
                engine="v2",
                session_id=session_id,
                turn_id=f"turn-{index}",
                model=model if isinstance(model, str) else None,
                requests=requests,
                cost=cost,
                start=start,
                end=end,
                state="closed",
                unpriced_reason=reason,
            ))

    return Session(
        engine="v2",
        session_id=session_id,
        path=path,
        cwd=cwd if isinstance(cwd, str) else None,
        model=session_model,
        turns=turns,
    )


def parse_session(path: Path) -> Session:
    """Parse a session on disk into a Session value (R10.1, R1.5).

    Never raises on bad data. An unrecognised path returns a Session with
    engine None and ``unknown_reason`` set.
    """
    path = Path(path)
    engine = detect_format(path)
    if engine == "v3":
        root = _v3_root(path) or path
        return _parse_v3(root, path)
    if engine == "v2":
        json_path = _v2_json_path(path) or path
        return _parse_v2(json_path, path)
    return Session(
        engine=None,
        session_id=path.stem,
        path=path,
        cwd=None,
        model=None,
        turns=[],
        unknown_reason="unknown format",
    )


# --- session discovery (R1.7) ----------------------------------------------


def _norm_dir(value: object) -> Optional[str]:
    """Normalise a directory string for comparison, or None.

    Expands ``~``, resolves ``.`` and ``..`` where possible, and drops a
    trailing separator so ``/a/b`` and ``/a/b/`` compare equal.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return str(Path(value).expanduser())
    except (OSError, RuntimeError, ValueError):
        return value.rstrip("/") or value


def _session_mtime(path: Path) -> float:
    """Newest mtime among the session's data files, 0.0 when none exist."""
    best = 0.0
    for candidate in (path, path / "messages.jsonl", path.with_suffix(".jsonl")):
        try:
            best = max(best, candidate.stat().st_mtime)
        except OSError:
            continue
    return best


def _v3_glob_by_id(kiro_home: Path, session_id: str,
                   project_hash: Optional[str]) -> Optional[Path]:
    """Find a V3 ``sess_*`` directory by id, newest project hash first."""
    name = session_id if session_id.startswith("sess_") else f"sess_{session_id}"
    sessions_root = kiro_home / "sessions"
    # Try the cached project hash first so later hooks skip the wide glob.
    if project_hash:
        candidate = sessions_root / project_hash / name
        if (candidate / "session.json").is_file():
            return candidate
    try:
        matches = sorted(sessions_root.glob(f"*/{name}"))
    except OSError:
        return None
    for candidate in matches:
        if (candidate / "session.json").is_file():
            return candidate
    return None


def _parse_index_at(value: object) -> float:
    """Epoch seconds from an index ``at`` value, 0.0 when missing or bad.

    The real index writes epoch milliseconds. Seconds and ISO 8601 strings
    are accepted too.
    """
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        # Epoch seconds stay below 1e11 until the year 5138.
        return number / 1000.0 if number > 1e11 else number
    parsed = _parse_time(value)
    return parsed.timestamp() if parsed is not None else 0.0


def _v3_index_candidates(kiro_home: Path) -> list[tuple[float, Path]]:
    """Session paths from the V3 index files, newest ``at`` first.

    Each index line is ``{"op", "sessionPath", "at"}``. ``sessionPath`` is
    relative to ``<kiro_home>/sessions`` (an absolute path is kept as is) and
    ``at`` is epoch milliseconds. A missing or bad ``at`` sorts last. Never
    raises.
    """
    sessions_root = kiro_home / "sessions"
    index_root = kiro_home / "session-index"
    found: dict[str, float] = {}
    try:
        index_files = sorted(index_root.glob("*.jsonl"))
    except OSError:
        return []
    for index_file in index_files:
        for event in _read_jsonl(index_file):
            session_path = event.get("sessionPath")
            if not isinstance(session_path, str) or not session_path:
                continue
            stamp = _parse_index_at(event.get("at"))
            resolved = Path(session_path).expanduser()
            if not resolved.is_absolute():
                resolved = sessions_root / resolved
            key = str(resolved)
            # Keep the newest "at" seen for each path.
            if key not in found or stamp > found[key]:
                found[key] = stamp
    ordered = sorted(found.items(), key=lambda item: item[1], reverse=True)
    return [(stamp, Path(path)) for path, stamp in ordered]


def _v3_by_project(kiro_home: Path, project: str) -> Optional[Path]:
    """Newest V3 session whose workspace contains the project directory."""
    for _stamp, raw in _v3_index_candidates(kiro_home):
        root = _v3_root(raw) or raw
        if not (root / "session.json").is_file():
            continue
        data = _load_session_json(root)
        workspaces = []
        for key in ("workspacePaths", "rootPaths"):
            value = data.get(key)
            if isinstance(value, list):
                workspaces.extend(value)
        if any(_norm_dir(w) == project for w in workspaces):
            return root
    return None


def _v2_by_cwd(kiro_home: Path, project: str) -> Optional[Path]:
    """Newest V2 ``cli`` session whose ``cwd`` equals the project directory."""
    cli_root = kiro_home / "sessions" / "cli"
    best: Optional[Path] = None
    best_stamp = ""
    try:
        json_files = sorted(cli_root.glob("*.json"))
    except OSError:
        return None
    for json_file in json_files:
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if _norm_dir(data.get("cwd")) != project:
            continue
        updated = data.get("updated_at")
        stamp = updated if isinstance(updated, str) else ""
        if best is None or stamp > best_stamp:
            best = json_file
            best_stamp = stamp
    return best


def find_session(project: Path, session_id: Optional[str] = None,
                 kiro_home: Optional[Path] = None,
                 v3_project_hash: Optional[str] = None) -> Optional[Path]:
    """Find the active session file for a project (R1.7).

    - V3 with ``session_id``: glob ``<kiro_home>/sessions/*/sess_<id>``,
      trying the cached ``v3_project_hash`` first.
    - V3 without an id: read the index tails, newest ``at`` first, and accept
      the session whose ``workspacePaths`` or ``rootPaths`` contains the
      project directory.
    - V2: the newest ``sessions/cli/*.json`` whose ``cwd`` equals the project.

    When a V3 and a V2 candidate both exist, the one with the newer activity
    wins. Returns the session path (a ``sess_*`` directory for V3, a
    ``<uuid>.json`` file for V2), or None. Never raises.
    """
    if kiro_home is None:
        kiro_home = Path.home() / ".kiro"
    kiro_home = Path(kiro_home)
    project_dir = _norm_dir(str(project))
    if project_dir is None:
        return None

    if session_id:
        v3 = _v3_glob_by_id(kiro_home, session_id, v3_project_hash)
        if v3 is not None:
            return v3

    v3_candidate = _v3_by_project(kiro_home, project_dir)
    v2_candidate = _v2_by_cwd(kiro_home, project_dir)

    if v3_candidate is not None and v2_candidate is not None:
        if _session_mtime(v3_candidate) >= _session_mtime(v2_candidate):
            return v3_candidate
        return v2_candidate
    return v3_candidate or v2_candidate


def v3_project_hash(session_path: Path) -> Optional[str]:
    """The project-hash directory name of a V3 session path, or None.

    This is the ``<hash>`` in ``<kiro_home>/sessions/<hash>/sess_<id>``, the
    value cached in ``state.json`` so later hooks skip the wide glob.
    """
    root = _v3_root(session_path)
    if root is None:
        return None
    return root.parent.name or None


# --- open-turn progress (R4.1, R5.1) ---------------------------------------


def _v3_open_requests(root: Path) -> int:
    """Assistant events in the newest open V3 turn (same rule as the parser).

    Counts ``assistant`` events carrying the turn id of the last ``turn_start``
    that has no matching ``usage_summary``. Returns 0 when there is no open
    turn. Never raises.
    """
    events = _read_jsonl(root / "messages.jsonl")
    closed: set[str] = set()
    last_open: Optional[str] = None
    order: list[str] = []
    seen: set[str] = set()
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        ptype = payload.get("type")
        turn_id = payload.get("turnId")
        if not isinstance(turn_id, str):
            continue
        if turn_id not in seen:
            seen.add(turn_id)
            order.append(turn_id)
        if ptype == "usage_summary":
            closed.add(turn_id)
    for turn_id in reversed(order):
        if turn_id not in closed:
            last_open = turn_id
            break
    if last_open is None:
        return 0
    count = 0
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "assistant" and payload.get("turnId") == last_open:
            count += 1
    return count


def _v2_open_requests(jsonl_path: Path, closed_requests: int) -> int:
    """Assistant-role lines in the V2 jsonl beyond the last closed turn.

    The ``.json`` carries ``total_request_count`` only at turn end, so while a
    turn runs we count the ``assistant``-role lines in the ``.jsonl`` past the
    requests already accounted for by closed turns. This is a HYPOTHESIS
    (design Open points 2 and 3) kept consistent with the closed-turn rule.
    Never raises.
    """
    assistant_lines = 0
    for event in _read_jsonl(jsonl_path):
        role = event.get("role")
        if role == "assistant":
            assistant_lines += 1
            continue
        # Some V2 lines nest the role under a message object.
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            assistant_lines += 1
    remaining = assistant_lines - closed_requests
    return remaining if remaining > 0 else 0


def open_turn_progress(path: Path) -> Optional[Progress]:
    """Live progress of the running turn for either engine (R4.1, R5.1).

    Returns a ``Progress`` of (requests so far, jsonl size in bytes, jsonl
    mtime). The size and mtime are of ``messages.jsonl`` (V3) or the
    ``<uuid>.jsonl`` (V2); they drive the pulse check in the watcher. Returns
    None when the path is not a recognised session or the jsonl is missing.
    Never raises.
    """
    path = Path(path)
    engine = detect_format(path)
    if engine == "v3":
        root = _v3_root(path) or path
        jsonl = root / "messages.jsonl"
        try:
            stat = jsonl.stat()
        except OSError:
            return None
        return Progress(_v3_open_requests(root), stat.st_size, stat.st_mtime)
    if engine == "v2":
        json_path = _v2_json_path(path) or path
        jsonl = json_path.with_suffix(".jsonl")
        try:
            stat = jsonl.stat()
        except OSError:
            return None
        closed = _v2_closed_requests(json_path)
        return Progress(_v2_open_requests(jsonl, closed), stat.st_size, stat.st_mtime)
    return None


def _credit_sum(value: object) -> Optional[float]:
    """``main + delegated`` of a ``creditSplit`` value, None when unusable."""
    if not isinstance(value, dict):
        return None
    total = 0.0
    for key in ("main", "delegated"):
        part = value.get(key, 0)
        if isinstance(part, bool) or not isinstance(part, (int, float)):
            return None
        total += float(part)
    return total


def live_credits(path: Path) -> Optional[float]:
    """Live credits of the open V3 turn from ``creditSplit`` metadata (R4.5).

    The figure is ``main + delegated`` of the latest ``creditSplit`` after the
    open turn's ``turn_start``, minus the same sum of the last ``creditSplit``
    before that ``turn_start`` (0 when there is none). Returns None for V2,
    when no turn is open, or when no ``creditSplit`` follows the turn start.
    Never raises.
    """
    path = Path(path)
    if detect_format(path) != "v3":
        return None
    root = _v3_root(path) or path
    events = _read_jsonl(root / "messages.jsonl")
    closed: set[str] = set()
    start_index: dict[str, int] = {}
    for index, event in enumerate(events):
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        turn_id = payload.get("turnId")
        if not isinstance(turn_id, str):
            continue
        if payload.get("type") == "turn_start":
            start_index[turn_id] = index
        elif payload.get("type") == "usage_summary":
            closed.add(turn_id)
    open_starts = [i for t, i in start_index.items() if t not in closed]
    if not open_starts:
        return None
    start = max(open_starts)
    before = 0.0
    after: Optional[float] = None
    for index, event in enumerate(events):
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "session_metadata" \
                or payload.get("key") != "creditSplit":
            continue
        total = _credit_sum(payload.get("value"))
        if total is None:
            continue
        if index < start:
            before = total
        else:
            after = total
    if after is None:
        return None
    return max(after - before, 0.0)


def _finite(value: object) -> bool:
    return isinstance(value, float) and value - value == 0.0


def series_from_events(events: list[dict]) -> list[dict]:
    """Each turn's live series and price from V3 events (R25.2, R25.4).

    A turn starts at its ``turn_start`` and runs up to the next one. Its series
    is ``main + delegated`` of every ``creditSplit`` in that range, minus the
    same sum of the last ``creditSplit`` before the turn start (0 when none),
    the rule of ``live_credits``. The price is the credit sum of the turn's
    ``usage_summary`` (``_v3_cost``). A turn with no usable ``creditSplit``
    gets one step at its price (0 when unpriced) and ``live`` False.
    Returns ``[{"credits": [...], "price": float or None, "live": bool}]``.
    The browser reader in ``tombstones.py`` mirrors this function.
    """
    order: list[str] = []
    start_at: dict[str, int] = {}
    prices: dict[str, Optional[float]] = {}
    splits: list[tuple[int, float]] = []
    for index, event in enumerate(events):
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        turn_id = payload.get("turnId")
        if kind == "turn_start" and isinstance(turn_id, str):
            if turn_id not in start_at:
                order.append(turn_id)
                start_at[turn_id] = index
        elif kind == "usage_summary" and isinstance(turn_id, str):
            cost = _v3_cost(payload)[0]
            prices[turn_id] = cost if cost is not None and _finite(cost) else None
        elif kind == "session_metadata" and payload.get("key") == "creditSplit":
            total = _credit_sum(payload.get("value"))
            if total is not None and _finite(total):
                splits.append((index, total))
    turns: list[dict] = []
    for position, turn_id in enumerate(order):
        start = start_at[turn_id]
        end = start_at[order[position + 1]] if position + 1 < len(order) else len(events)
        before = 0.0
        for index, total in splits:
            if index < start:
                before = total
        credits = [max(total - before, 0.0) for index, total in splits
                   if start < index < end]
        price = prices.get(turn_id)
        live = bool(credits)
        if not live:
            credits = [price if price is not None else 0.0]
        turns.append({"credits": credits, "price": price, "live": live})
    return turns


def turn_series(path: Path) -> list[dict]:
    """``series_from_events`` for a V3 ``sess_*`` directory; ``[]`` for anything else.

    Never raises.
    """
    path = Path(path)
    if detect_format(path) != "v3":
        return []
    root = _v3_root(path) or path
    return series_from_events(_read_jsonl(root / "messages.jsonl"))


def first_over(credits: list[float], cap: float) -> Optional[int]:
    """1-based first step whose live figure is above ``cap`` (the watcher's rule), or None."""
    for step, figure in enumerate(credits, start=1):
        if figure > cap:
            return step
    return None


def assistant_counts(path: Path) -> dict[str, int]:
    """Assistant events per turn id of a V3 session (the watcher's unit).

    This is the same count the parser uses for an open turn. On a closed turn
    it overcounts the real requests (``len(requestIds)``), so the watcher
    never prices it as a request: it calibrates credits per counted unit from
    closed turns instead. V2 sessions and unreadable paths give ``{}``.
    Never raises.
    """
    path = Path(path)
    if detect_format(path) != "v3":
        return {}
    root = _v3_root(path) or path
    counts: dict[str, int] = {}
    for event in _read_jsonl(root / "messages.jsonl"):
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "assistant":
            continue
        turn_id = payload.get("turnId")
        if isinstance(turn_id, str):
            counts[turn_id] = counts.get(turn_id, 0) + 1
    return counts


def _v2_closed_requests(json_path: Path) -> int:
    """Sum of ``total_request_count`` over the closed turns of a V2 session."""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    state = data.get("session_state")
    cm = state.get("conversation_metadata") if isinstance(state, dict) else None
    metadatas = cm.get("user_turn_metadatas") if isinstance(cm, dict) else None
    if not isinstance(metadatas, list):
        return 0
    total = 0
    for meta in metadatas:
        if isinstance(meta, dict) and isinstance(meta.get("total_request_count"), int):
            total += meta["total_request_count"]
    return total
