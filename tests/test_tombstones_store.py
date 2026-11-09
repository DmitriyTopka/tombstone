"""Tests for the locked JSON store and tombstones.json helpers (task 3.1)."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from tombstone.tombstones import (
    apply_pending_record,
    load_tombstones,
    update_json,
    upsert_record,
)


def _project(tmp_path: Path) -> Path:
    """A project directory with the .tombstone sidecar in place."""
    (tmp_path / ".tombstone").mkdir(parents=True)
    return tmp_path


def _state_path(tmp_path: Path) -> Path:
    return _project(tmp_path) / ".tombstone" / "state.json"


# --- update_json basics ----------------------------------------------------

def test_creates_file_from_default(tmp_path):
    path = _state_path(tmp_path)
    result = update_json(path, lambda d: {**d, "n": 1}, default={"n": 0})
    assert result == {"n": 1}
    assert json.loads(path.read_text()) == {"n": 1}


def test_reads_existing_then_updates(tmp_path):
    path = _state_path(tmp_path)
    path.write_text(json.dumps({"n": 5}))
    result = update_json(path, lambda d: {**d, "n": d["n"] + 1}, default={"n": 0})
    assert result == {"n": 6}
    assert json.loads(path.read_text()) == {"n": 6}


def test_atomic_write_leaves_no_temp_files(tmp_path):
    path = _state_path(tmp_path)
    update_json(path, lambda d: {**d, "n": 1}, default={})
    sidecar = tmp_path / ".tombstone"
    leftovers = [p.name for p in sidecar.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_lock_file_created_in_sidecar(tmp_path):
    path = _state_path(tmp_path)
    update_json(path, lambda d: d, default={})
    assert (tmp_path / ".tombstone" / "state.json.lock").exists()


# --- invalid-JSON path ------------------------------------------------------

def test_invalid_json_file_left_untouched_and_queued(tmp_path):
    path = _state_path(tmp_path)
    broken = "{ this is not json "
    path.write_text(broken)

    result = update_json(path, lambda d: {**d, "n": 1}, default={"n": 0})

    # The broken file on disk is not overwritten.
    assert path.read_text() == broken
    # The caller still sees the value it meant to write.
    assert result == {"n": 1}
    # The change is queued in pending.jsonl.
    pending = tmp_path / ".tombstone" / "pending.jsonl"
    assert pending.exists()
    lines = [json.loads(ln) for ln in pending.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0]["target"] == "state.json"
    assert lines[0]["change"] == {"n": 1}
    # A warning is logged.
    log = (tmp_path / ".tombstone" / "errors.log").read_text()
    assert "not valid JSON" in log


def test_pending_merged_on_next_successful_write(tmp_path):
    path = _state_path(tmp_path)
    path.write_text("not json at all")

    # First write is queued because the file is corrupt.
    update_json(
        path,
        lambda d: upsert_record(d, {"id": "a", "turns": []}),
        default={"version": 1, "tombstones": []},
        pending_apply=apply_pending_record,
    )

    # The user repairs the file to a valid empty store.
    path.write_text(json.dumps({"version": 1, "tombstones": []}))

    # The next write folds in the queued change plus the new one.
    update_json(
        path,
        lambda d: upsert_record(d, {"id": "b", "turns": []}),
        default={"version": 1, "tombstones": []},
        pending_apply=apply_pending_record,
    )

    store = json.loads(path.read_text())
    ids = {r["id"] for r in store["tombstones"]}
    assert ids == {"a", "b"}
    # The pending queue is drained.
    pending = tmp_path / ".tombstone" / "pending.jsonl"
    assert not pending.exists() or pending.read_text().strip() == ""


# --- concurrency ------------------------------------------------------------

def _increment_worker(path_str: str) -> None:
    from tombstone.tombstones import update_json as uj

    uj(Path(path_str), lambda d: {**d, "n": d.get("n", 0) + 1}, default={"n": 0})


def test_twenty_concurrent_writers_keep_valid_json(tmp_path):
    path = _state_path(tmp_path)
    path.write_text(json.dumps({"n": 0}))

    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_increment_worker, args=(str(path),)) for _ in range(20)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=30)

    for proc in procs:
        assert proc.exitcode == 0

    # The file is still valid JSON and every writer's increment landed.
    data = json.loads(path.read_text())
    assert data["n"] == 20


# --- tombstones.json helpers ------------------------------------------------

def test_load_tombstones_missing_file_is_empty_store(tmp_path):
    store = load_tombstones(tmp_path / "tombstones.json")
    assert store == {"version": 1, "tombstones": []}


def test_load_tombstones_corrupt_file_is_empty_store_and_untouched(tmp_path):
    path = tmp_path / "tombstones.json"
    broken = "{bad"
    path.write_text(broken)
    store = load_tombstones(path)
    assert store == {"version": 1, "tombstones": []}
    # A read never modifies the file.
    assert path.read_text() == broken


def test_upsert_inserts_new_record(tmp_path):
    store = {"version": 1, "tombstones": []}
    upsert_record(store, {"id": "tombstone/3.1", "turns": []})
    assert len(store["tombstones"]) == 1
    assert store["tombstones"][0]["id"] == "tombstone/3.1"


def test_upsert_is_idempotent_by_id_and_turn(tmp_path):
    store = {"version": 1, "tombstones": []}
    record = {
        "id": "tombstone/3.1",
        "requests": 5,
        "turns": [{"turn_id": "t1", "requests": 5}],
    }
    upsert_record(store, record)
    upsert_record(store, record)
    assert len(store["tombstones"]) == 1
    assert len(store["tombstones"][0]["turns"]) == 1


def test_upsert_updates_fields_and_adds_new_turn(tmp_path):
    store = {"version": 1, "tombstones": []}
    upsert_record(
        store,
        {"id": "x", "actual": None, "turns": [{"turn_id": "t1", "actual": None}]},
    )
    upsert_record(
        store,
        {"id": "x", "actual": 0.5, "turns": [{"turn_id": "t2", "actual": 0.5}]},
    )
    assert len(store["tombstones"]) == 1
    record = store["tombstones"][0]
    assert record["actual"] == 0.5
    turn_ids = {t["turn_id"] for t in record["turns"]}
    assert turn_ids == {"t1", "t2"}


def test_upsert_through_update_json_round_trip(tmp_path):
    path = _project(tmp_path) / "tombstones.json"
    update_json(
        path,
        lambda d: upsert_record(d, {"id": "a", "turns": []}),
        default={"version": 1, "tombstones": []},
        pending_apply=apply_pending_record,
    )
    reloaded = load_tombstones(path)
    assert [r["id"] for r in reloaded["tombstones"]] == ["a"]
