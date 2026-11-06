"""Tests for the init builders (task 10.1)."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from tombstone import caps, cli

EXE = "/opt/bin/python"


def _commands(files: dict) -> dict:
    return {name: f["hooks"][0] for name, f in files.items()}


def test_v3_hook_set_and_shape(tmp_path: Path) -> None:
    files = cli.build_v3_hook_files(EXE, tmp_path)
    triggers = {h["trigger"] for h in _commands(files).values()}
    assert triggers == {"PreToolUse", "PostToolUse", "Stop", "SessionStart",
                        "UserPromptSubmit", "PreTaskExecution",
                        "PostTaskExecution"}
    for name, hook in _commands(files).items():
        assert name == hook["name"] + ".json"
        assert name.startswith("tombstone-")
        assert len(files[name]["hooks"]) == 1
        assert files[name]["version"] == "v1"
        assert hook["action"]["type"] == "command"
        assert hook["action"]["timeout"] == 10
        json.dumps(files[name])


def test_v3_commands_call_events_with_project(tmp_path: Path) -> None:
    files = cli.build_v3_hook_files(EXE, tmp_path)
    by_trigger = {h["trigger"]: h["action"]["command"]
                  for h in _commands(files).values()}
    expected = {"PreToolUse": "pre-tool", "PostToolUse": "post-tool",
                "Stop": "stop", "SessionStart": "session-start",
                "UserPromptSubmit": "prompt", "PreTaskExecution": "task-pre",
                "PostTaskExecution": "task-post"}
    for trigger, event in expected.items():
        parts = shlex.split(by_trigger[trigger])
        assert parts == [EXE, "-m", "tombstone", "hook", event, "--project",
                         str(tmp_path.resolve())]


def test_paths_with_spaces_are_quoted(tmp_path: Path) -> None:
    project = tmp_path / "my project"
    project.mkdir()
    command = cli.hook_command("/opt/my bin/python", "stop", project)
    assert shlex.split(command)[0] == "/opt/my bin/python"
    assert shlex.split(command)[-1] == str(project.resolve())


def test_hook_commands_run_through_sh_with_spaces(tmp_path: Path) -> None:
    """Generated commands run under ``sh -c`` with a space in both paths."""
    import os
    import subprocess
    import sys

    bindir = tmp_path / "py bin"
    bindir.mkdir()
    python = bindir / "python"
    python.symlink_to(sys.executable)
    project = tmp_path / "Новая папка 2"
    (project / ".kiro").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    src = Path(__file__).resolve().parent.parent / "src"
    env = {**os.environ, "HOME": str(home), "PYTHONPATH": str(src)}
    files = cli.build_v3_hook_files(str(python), project)
    for name, data in files.items():
        if name == "tombstone-stop.json":
            continue  # spawns a detached reconcile child, covered elsewhere
        command = data["hooks"][0]["action"]["command"]
        assert command.startswith(f"'{python}' -m tombstone hook ")
        done = subprocess.run(["sh", "-c", command], input='{"cwd": "."}',
                              capture_output=True, text=True, env=env,
                              timeout=30, check=False)
        assert done.returncode == 0, (name, done.stderr)


def test_v2_agent_config(tmp_path: Path) -> None:
    config = cli.build_v2_agent_config(EXE, tmp_path)
    assert "model" not in config
    assert set(config["hooks"]) == {"preToolUse", "postToolUse", "stop"}
    for entry in config["hooks"].values():
        assert len(entry) == 1
        assert "--project" in entry[0]["command"]
        assert entry[0]["command"].startswith(f"'{EXE}' -m tombstone hook ")


def test_find_executable_is_sys_executable() -> None:
    import sys

    assert cli.find_executable() == sys.executable


def test_mcp_entry_shape(tmp_path: Path) -> None:
    assert cli.build_mcp_entry("/opt/py", tmp_path) == {
        "command": "/opt/py",
        "args": ["-m", "tombstone", "mcp", "--project", str(tmp_path.resolve())]}


def test_merge_mcp_creates_file(tmp_path: Path) -> None:
    path = tmp_path / ".kiro" / "settings" / "mcp.json"
    assert cli.merge_mcp_settings(path, "/opt/py", tmp_path).startswith("created ")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"mcpServers": {"tombstone": cli.build_mcp_entry("/opt/py", tmp_path)}}


def test_merge_mcp_keeps_other_content(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"other": 1, "mcpServers": {"x": {"command": "x"}}}),
                    encoding="utf-8")
    assert cli.merge_mcp_settings(path, "/opt/py", tmp_path).startswith("updated ")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["other"] == 1 and data["mcpServers"]["x"] == {"command": "x"}
    assert data["mcpServers"]["tombstone"]["command"] == "/opt/py"


def test_merge_mcp_leaves_existing_tombstone_entry(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    text = json.dumps({"mcpServers": {"tombstone": {"command": "mine"}}})
    path.write_text(text, encoding="utf-8")
    assert "already registered" in cli.merge_mcp_settings(path, "/opt/py", tmp_path)
    assert path.read_text(encoding="utf-8") == text


def test_merge_mcp_leaves_invalid_file(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text("{nope", encoding="utf-8")
    assert "left unchanged" in cli.merge_mcp_settings(path, "/opt/py", tmp_path)
    assert path.read_text(encoding="utf-8") == "{nope"


def test_only_command_actions_no_model_anywhere(tmp_path: Path) -> None:
    blob = json.dumps([cli.build_v3_hook_files(EXE, tmp_path),
                       cli.build_v2_agent_config(EXE, tmp_path)])
    assert '"type": "agent"' not in blob
    assert '"prompt"' not in blob
    assert '"model"' not in blob


def test_steering_text() -> None:
    text = cli.build_steering_text()
    assert "TOMBSTONE:" in text
    assert "public" in text
    assert "get_budget" in text


def test_default_toml_loads_without_warnings(tmp_path: Path) -> None:
    (tmp_path / "tombstone.toml").write_text(cli.build_default_toml(),
                                             encoding="utf-8")
    config = caps.load_config(tmp_path)
    assert config.warnings == ()
    assert config.avg_request_price == 0.25
    assert config.task_cap == 40.0
    assert config.spec_cap == 300.0
    assert config.quiet_window_minutes == 10.0
    assert config.min_plan_balance == 0.0
    assert config.kill_grace_seconds == 30.0


def test_no_dash_characters(tmp_path: Path) -> None:
    blob = (cli.build_steering_text() + cli.build_default_toml()
            + json.dumps(cli.build_v3_hook_files(EXE, tmp_path),
                         ensure_ascii=False)
            + json.dumps(cli.build_v2_agent_config(EXE, tmp_path),
                         ensure_ascii=False))
    assert "\u2014" not in blob and "\u2013" not in blob


def test_find_executable_returns_a_string() -> None:
    assert isinstance(cli.find_executable(), str)
