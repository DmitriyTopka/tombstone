---
name: tombstone
description: Plan within a credit budget. Call get_budget before each spec task and treat a TOMBSTONE: refusal as a hard stop.
---

# Tombstone budget skill

Tombstone is a circuit breaker that caps credits per task and per spec. The caps are public, in `tombstone.toml` and in the `get_budget` answer.

## get_budget

Before each spec task, call the MCP tool `get_budget` with the task name. It returns three groups of numbers, all in credits:

- `similar`: the median cost of similar finished tasks, with `basis` (`actual` or `est.`), plus `task_cap`.
- `spec`: the spec `cap`, `spent`, `remaining` and `basis`.
- `plan_balance`: the plan `value` (actual), `age_minutes` and a `reason` when it is unavailable.

If `spec.remaining` is lower than `similar.median`, do not start the task. Tell the human the numbers and ask how to proceed. If `plan_balance.value` is null or older than 30 minutes, say so in one line and continue. Keep the model you were given and never suggest another one to save credits.

## A TOMBSTONE: refusal

A tool call refused with a message that starts with `TOMBSTONE:` means stop. Do not retry the tool, do not try another tool to get around it, and do not edit the cap or anything in `.kiro/tombstone/`, `tombstone.toml` or `tombstones.json`. Quote the message to the human and wait. The human removes the block with `tombstone resume`.

## Hooks are not part of the power

Hooks are not a documented power component. The files in `power/hooks/` (`v3/*.json` and `v2/tombstone.json`) are reference templates generated from the same builders as `tombstone init`, with placeholder paths (`/path/to/python`, `/path/to/project`). `tombstone init` installs the real hook files into `.kiro/hooks/` and registers the MCP server in `.kiro/settings/mcp.json` of the project, both with the absolute interpreter path. It writes `.kiro/agents/tombstone.json` only with `--v2`, because Kiro CLI 3.1.0 loads that file as the active profile and that profile has no MCP tools. Installing the power alone does not enable the cap gate.
