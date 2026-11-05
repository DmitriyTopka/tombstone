"""Scrub a Kiro V2 (legacy cli) session into a test fixture.

Removes home paths and all human-readable message text while keeping the
structure the parser needs: session_id, a fake cwd, created_at / updated_at,
and the conversation_metadata.user_turn_metadatas entries with their
total_request_count, metering_usage (unit, unitPlural, value), model,
end_timestamp and turn_duration.

Usage:
    python tests/fixtures/scrub_session_v2.py <session.json> <out.json>

The source session is read only. Nothing outside out.json is written.
The matching .jsonl is written next to out.json as an empty file, because
the real .jsonl holds only message text that a closed-turn fixture does not
need.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLACEHOLDER = "scrubbed"
FAKE_CWD = "/workspace/project0"

# Turn fields the parser reads. Everything else is dropped so no message
# text, token counts or result payload can leak.
KEPT_TURN_KEYS = (
    "total_request_count",
    "metering_usage",
    "model",
    "end_timestamp",
    "turn_duration",
    "end_reason",
)


def scrub_metering(entries: object) -> list[dict]:
    out: list[dict] = []
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kept = {"unit": entry.get("unit"), "value": entry.get("value")}
        if "unitPlural" in entry:
            kept["unitPlural"] = entry["unitPlural"]
        out.append(kept)
    return out


def scrub_turn(turn: dict) -> dict:
    out: dict = {}
    for key in KEPT_TURN_KEYS:
        if key not in turn:
            continue
        if key == "metering_usage":
            out[key] = scrub_metering(turn[key])
        else:
            out[key] = turn[key]
    return out


def scrub_session_json(src: Path, dst: Path) -> None:
    data = json.loads(src.read_text(encoding="utf-8"))
    state = data.get("session_state", {})
    cm = state.get("conversation_metadata", {}) if isinstance(state, dict) else {}
    turns = cm.get("user_turn_metadatas") if isinstance(cm, dict) else None
    scrubbed_turns = [scrub_turn(t) for t in turns if isinstance(t, dict)] \
        if isinstance(turns, list) else []
    kept = {
        "session_id": data.get("session_id"),
        "cwd": FAKE_CWD,
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "title": PLACEHOLDER,
        "session_state": {
            "version": state.get("version") if isinstance(state, dict) else None,
            "agent_name": state.get("agent_name") if isinstance(state, dict) else None,
            "conversation_metadata": {
                "user_turn_metadatas": scrubbed_turns,
            },
        },
    }
    dst.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    src = Path(argv[1])
    dst = Path(argv[2])
    dst.parent.mkdir(parents=True, exist_ok=True)
    scrub_session_json(src, dst)
    # Closed-turn fixture: the .jsonl holds only message text. An empty file
    # keeps the V2 layout (a .json plus its .jsonl sibling) with no text.
    dst.with_suffix(".jsonl").write_text("", encoding="utf-8")
    print(f"wrote {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
