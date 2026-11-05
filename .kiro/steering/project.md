---
inclusion: always
---

# Project rules

## Stack

- Python 3.11 or newer. Standard library only, plus the `mcp` package for the `get_budget` server. No other runtime dependency.
- `tomllib` for config, `json` for session files. `subprocess` only for this list: `git status`, `osascript`, `notify-send`, `kiro-cli chat --no-interactive "/usage"` (cached 5 minutes), `kiro-cli --version`, `ps` and `lsof` (macOS process lookup for the kill step), and `launchctl` / `systemctl --user` called only by `tombstone init` to load the watcher unit. Linux process lookup reads `/proc`.
- The watcher is one process. macOS: launchd plist. Linux: systemd user unit.
- The graveyard is one static HTML file with inline CSS and JS. No libraries, no CDN, no fonts from the network.
- Tests: `pytest` as a dev dependency only. Session fixtures live in `tests/fixtures/`, with home paths removed.

## Style

- Small modules with one job each: `sessions`, `tombstones`, `caps`, `watcher`, `stop`, `autopsy`, `mcp_server`, `cli`.
- Type hints on public functions. Plain functions over classes unless state is real.
- Every number shown to a human says whether it is `est.` or `actual`.
- Hooks must finish in under 200 ms when there is nothing to block.
- Never write outside the project directory except the launchd or systemd unit at init.

## Hard rules

- No agent hooks (prompt-type hooks). Only shell command hooks. Tombstone must spend zero credits.
- No network calls. The only external command is the local `kiro-cli` `/usage` probe, cached for 5 minutes.
- Never change or suggest the agent model.
- Never delete or revert user files.

## Language

- English in code, comments, commit messages, README, docs and CLI output.
- No em dash or en dash characters anywhere. Use a plain hyphen or a full stop.
