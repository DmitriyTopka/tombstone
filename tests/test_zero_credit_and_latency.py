"""Task 11.4: hook latency, zero-credit static checks, style gate, accuracy.

R11.5 (latency), R11.2 and R11.4 (no model call, no network), R4.2
(estimate within 30% of actual), plus the no em or en dash style gate.
"""

from __future__ import annotations

import ast
import json
import os
import statistics
import subprocess
import time
from pathlib import Path

import pytest

from tombstone import tombstones
from tombstone.sessions import parse_session

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "tombstone"
FIXTURES = ROOT / "tests" / "fixtures"
EXECUTABLE = ROOT / ".venv" / "bin" / "tombstone"

# Built from code points so this file holds no dash character itself.
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)


# --- (a) hook latency --------------------------------------------------------


@pytest.mark.skipif(not EXECUTABLE.exists(), reason="project virtualenv missing")
def test_pre_tool_hook_median_under_200ms(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    assert not (project / "STOP").exists()
    assert not (project / ".kiro" / "tombstone" / "STOP").exists()
    env = dict(os.environ, XDG_STATE_HOME=str(tmp_path / "state"))

    timings = []
    for _ in range(5):
        start = time.perf_counter()
        done = subprocess.run(
            [str(EXECUTABLE), "hook", "pre-tool", "--project", str(project)],
            input="{}", capture_output=True, text=True, cwd=str(tmp_path),
            env=env, timeout=10)
        timings.append(time.perf_counter() - start)
        assert done.returncode == 0, done.stderr
    median_ms = statistics.median(timings) * 1000.0
    assert median_ms < 200.0, f"median {median_ms:.0f} ms, runs {timings}"


# --- (b) static scan: no network, no model ------------------------------------

NETWORK_MODULES = {
    "socket", "socketserver", "ssl", "urllib", "urllib3", "http", "httplib",
    "requests", "httpx", "aiohttp", "ftplib", "smtplib", "poplib", "imaplib",
    "telnetlib", "xmlrpc", "websocket", "websockets", "asyncio",
}

# The only two kiro-cli commands the package may run (R8.3, watcher version).
ALLOWED_KIRO_COMMANDS = {
    ("kiro-cli", "chat", "--no-interactive", "/usage"),
    ("kiro-cli", "--version"),
}

SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}
FORBIDDEN_OS_CALLS = {"system", "popen", "execv", "execve", "execvp", "execvpe",
                      "execl", "execle", "execlp", "execlpe", "spawnl",
                      "spawnv", "spawnlp", "spawnvp"}


def _source_trees():
    for path in sorted(SRC.glob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_package_has_modules_to_scan():
    assert len(list(SRC.glob("*.py"))) >= 10


def test_no_network_module_imports():
    offenders = []
    for path, tree in _source_trees():
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in NETWORK_MODULES:
                    offenders.append(f"{path.name}:{node.lineno} imports {name}")
    assert not offenders, offenders


def test_no_dynamic_import_of_network_modules():
    offenders = []
    for path, tree in _source_trees():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, (ast.Name, ast.Attribute))
                    and getattr(node.func, "id", getattr(node.func, "attr", ""))
                    in {"__import__", "import_module"}):
                for arg in node.args[:1]:
                    if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                            and arg.value.split(".")[0] in NETWORK_MODULES):
                        offenders.append(f"{path.name}:{node.lineno} {arg.value}")
    assert not offenders, offenders


def _module_constants(tree: ast.Module) -> dict:
    """Module-level ``NAME = [str, ...]`` assignments, for resolving a Name."""
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            value = _literal_strings(node.value, {})
            if value is not None:
                found[node.targets[0].id] = value
    return found


def _literal_strings(node, constants: dict):
    """A list of string constants for a list/tuple literal or known Name."""
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, (ast.List, ast.Tuple)):
        if all(isinstance(e, ast.Constant) and isinstance(e.value, str)
               for e in node.elts):
            return [e.value for e in node.elts]
    return None


def _is_subprocess_call(node: ast.Call) -> bool:
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr in SUBPROCESS_FUNCS
            and isinstance(func.value, ast.Name) and func.value.id == "subprocess")


def test_no_subprocess_runs_a_model_or_agent():
    offenders = []
    seen_kiro = set()
    for path, tree in _source_trees():
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _is_subprocess_call(node)):
                continue
            if not node.args:
                offenders.append(f"{path.name}:{node.lineno} subprocess call without args")
                continue
            first = node.args[0]
            literal = _literal_strings(first, constants)
            if literal is None:
                # Built at run time. Reject any shell string naming kiro-cli.
                for sub in ast.walk(first):
                    if (isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                            and "kiro-cli" in sub.value):
                        offenders.append(f"{path.name}:{node.lineno} dynamic kiro-cli call")
                continue
            if any(os.path.basename(item) == "kiro-cli" for item in literal[:1]):
                command = tuple(literal)
                if command not in ALLOWED_KIRO_COMMANDS:
                    offenders.append(f"{path.name}:{node.lineno} runs {command}")
                else:
                    seen_kiro.add(command)
            for item in literal:
                if item in {"--agent", "--model"} or item.startswith("--model="):
                    offenders.append(f"{path.name}:{node.lineno} passes {item}")
    assert not offenders, offenders
    # Self-check: the scan really sees the two permitted calls.
    assert seen_kiro == ALLOWED_KIRO_COMMANDS


def test_no_shell_exec_helpers_in_package():
    offenders = []
    for path, tree in _source_trees():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and node.func.attr in FORBIDDEN_OS_CALLS):
                offenders.append(f"{path.name}:{node.lineno} os.{node.func.attr}")
    assert not offenders, offenders


def test_static_scan_catches_a_planted_violation():
    """The scanner is not vacuous: a planted bad call is flagged."""
    tree = ast.parse("import subprocess\n"
                     "subprocess.run(['kiro-cli', 'chat', 'hello'])\n")
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and _is_subprocess_call(n)]
    assert len(calls) == 1
    command = tuple(_literal_strings(calls[0].args[0], {}))
    assert command not in ALLOWED_KIRO_COMMANDS


# --- (c) style gate: no em or en dash in text files ----------------------------

TEXT_SUFFIXES = {".py", ".md", ".json", ".toml", ".html", ".txt"}
SKIP_DIRS = {".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
             ".mypy_cache", ".ruff_cache", "build", "dist", "htmlcov",
             ".idea", "node_modules"}


def _repo_text_files():
    for current, dirs, files in os.walk(ROOT):
        current_path = Path(current)
        rel = current_path.relative_to(ROOT).parts
        dirs[:] = [d for d in dirs
                   if d not in SKIP_DIRS
                   and not d.endswith(".egg-info")
                   and not (rel == (".kiro",) and d == "tombstone")]
        for name in files:
            path = current_path / name
            if path.suffix in TEXT_SUFFIXES:
                yield path


def test_no_em_or_en_dash_in_any_text_file():
    files = list(_repo_text_files())
    assert any(p.name == "cli.py" for p in files)
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if EM_DASH in line or EN_DASH in line:
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, offenders[:20]


# --- (d) estimate accuracy on the 10-request fixture ----------------------------

MEASUREMENT2 = FIXTURES / "v2_measurement2" / "b7b3fa2c-07c1-4f70-b486-b566c4860883.json"
TEN_REQUEST = FIXTURES / "v2_ten_request" / "ten.json"


def test_estimate_within_30_percent_on_ten_request_fixture():
    """R4.2: requests x avg price, the average learned from earlier turns."""
    calibration = parse_session(MEASUREMENT2).turns[0]
    state: dict = {}
    tombstones.update_price_window(state, calibration.requests, calibration.cost)
    avg_price = tombstones._avg_price(state, tombstones.DEFAULT_AVG_REQUEST_PRICE)
    assert avg_price == pytest.approx(calibration.cost / calibration.requests,
                                      rel=1e-3)

    turn = parse_session(TEN_REQUEST).turns[0]
    assert turn.requests == 10
    assert turn.cost is not None and turn.cost > 0

    estimate = turn.requests * avg_price      # est.
    actual = turn.cost                        # actual
    error = abs(estimate - actual) / actual
    assert error <= 0.30, f"est. {estimate:.3f} vs actual {actual:.3f} ({error:.0%})"


V3_MEASUREMENT5 = (FIXTURES / "v3_measurement5"
                   / "sess_8b5bdb0f-3662-4dca-be63-268a0365f3d9")


def test_v3_estimate_within_30_percent_on_real_fixture():
    """R4.1: units x avg_unit_price, calibrated on a closed V3 turn.

    The real fixture has 9 counted units but 5 requests. Pricing units with the
    per-request average overshoots by more than 30%; the calibrated unit price
    (the hooks fold the closed turn without the watcher) stays within 30%.
    """
    from tombstone import sessions

    turn = parse_session(V3_MEASUREMENT5).turns[0]
    units = sessions.assistant_counts(V3_MEASUREMENT5)[turn.turn_id]
    assert units > turn.requests and turn.cost is not None and turn.cost > 0

    state: dict = {}
    tombstones.fold_priced_turn(state, turn, V3_MEASUREMENT5)
    assert state["avg_unit_price"] is not None

    estimate = units * state["avg_unit_price"]            # est.
    actual = turn.cost                                    # actual
    assert abs(estimate - actual) / actual <= 0.30

    per_request = units * state["avg_request_price"]      # est., the old fallback
    assert abs(per_request - actual) / actual > 0.30


def test_ten_request_fixture_is_valid_json_without_home_path():
    text = TEN_REQUEST.read_text(encoding="utf-8")
    json.loads(text)
    assert "/Users/" not in text and "/home/" not in text
