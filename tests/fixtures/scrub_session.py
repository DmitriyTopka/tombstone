"""Scrub a Kiro V3 session into a test fixture.

Removes home paths and all human-readable message text while keeping the
structure the parser needs: payload types, turnId, promptTurnSummaries with
unit and usage, requestIds, timestamps, modelId and workspacePaths shape.

Usage:
    python tests/fixtures/scrub_session.py <session_dir> <out_dir>

The source session is read only. Nothing outside out_dir is written.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Payload keys that carry free text or secrets and must be blanked.
TEXT_KEYS = {"content", "question", "title", "value"}

# Fields on a user payload that may carry attachments.
DROP_KEYS = {"documents", "images", "_meta", "options", "selectedOption", "context"}

PLACEHOLDER = "scrubbed"

# Keys that hold a filesystem path. Any absolute path string also gets
# replaced regardless of key, see scrub_paths below.
PATH_KEYS = {"filePath", "path", "hookId", "sessionPath"}


def _fake_path(index: int) -> str:
    """A stable fake absolute path with no home information."""
    return f"/workspace/project{index}"


def scrub_paths(value: object) -> object:
    """Replace any absolute-path-looking string leaf with a fake path."""
    if isinstance(value, str):
        if value.startswith("/") or value.startswith("~"):
            return _fake_path(0)
        return value
    if isinstance(value, list):
        return [scrub_paths(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub_paths(v) for k, v in value.items()}
    return value


def scrub_text(value: object) -> object:
    """Replace any string leaf with a placeholder, recurse into containers."""
    if isinstance(value, str):
        return PLACEHOLDER
    if isinstance(value, list):
        return [scrub_text(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub_text(v) for k, v in value.items()}
    return value


def scrub_payload(payload: dict) -> dict:
    out: dict = {}
    for key, value in payload.items():
        if key in DROP_KEYS:
            continue
        if key == "type":
            out[key] = value
            continue
        if key == "args":
            # Tool arguments can hold paths and text. Blank the whole field.
            out[key] = PLACEHOLDER
            continue
        if key == "promptTurnSummaries":
            # Keep unit and usage, blank usedTools text.
            summaries = []
            for summary in value:
                kept = {
                    "unit": summary.get("unit"),
                    "usage": summary.get("usage"),
                }
                if "unitPlural" in summary:
                    kept["unitPlural"] = summary["unitPlural"]
                summaries.append(kept)
            out[key] = summaries
            continue
        if key in PATH_KEYS:
            out[key] = _fake_path(0)
            continue
        if key in TEXT_KEYS:
            out[key] = scrub_text(value)
            continue
        out[key] = value
    # Final pass: catch any path-like string that slipped through under an
    # unexpected key, so no home information can leak.
    return scrub_paths(out)


def scrub_messages(src: Path, dst: Path) -> None:
    with src.open(encoding="utf-8") as fh:
        lines = [line.rstrip("\n") for line in fh]
    scrubbed: list[str] = []
    for line in lines:
        if not line.strip():
            scrubbed.append("")
            continue
        obj = json.loads(line)
        payload = obj.get("payload", {})
        new_obj = {
            "id": PLACEHOLDER,
            "timestamp": obj.get("timestamp"),
            "payload": scrub_payload(payload),
        }
        scrubbed.append(json.dumps(new_obj, separators=(",", ":")))
    dst.write_text("\n".join(scrubbed) + "\n", encoding="utf-8")


def scrub_session_json(src: Path, dst: Path) -> None:
    data = json.loads(src.read_text(encoding="utf-8"))
    kept = {
        "schemaVersion": data.get("schemaVersion"),
        "dataModelVersion": data.get("dataModelVersion"),
        "id": data.get("id"),
        "title": PLACEHOLDER,
        "agentMode": data.get("agentMode"),
        "workspacePaths": [_fake_path(0)],
        "rootPaths": [_fake_path(0)],
        "createdAt": data.get("createdAt"),
        "lastModifiedAt": data.get("lastModifiedAt"),
        "modelId": data.get("modelId"),
        "status": data.get("status"),
    }
    dst.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    session_dir = Path(argv[1])
    out_dir = Path(argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    scrub_session_json(session_dir / "session.json", out_dir / "session.json")
    scrub_messages(session_dir / "messages.jsonl", out_dir / "messages.jsonl")
    print(f"wrote {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
