"""Static check: every file write or delete in src/ sits in a guarded function.

Walks the AST of ``src/tombstone/*.py`` and finds calls to write_text,
write_bytes, open or fdopen with a write mode, os.open, os.replace,
os.rename, os.remove, unlink, rmdir, rmtree and mkstemp. The enclosing function must be
in an allowlist. A DIRECT entry must itself call ``guard`` or
``is_safe_target``. A CALLER_GUARDED entry must only be called from DIRECT
entries. An OUTSIDE entry writes outside the project on purpose.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "tombstone"

DIRECT = {
    "stop.py:write_stop",
    "stop.py:resume",
    "stop.py:_log_error",
    "tombstones.py:update_json",
    "tombstones.py:_log_warning",
    "tombstones.py:spawn_reconcile",
    "tombstones.py:reconcile_log",
    "tombstones.py:_log_reconcile_timeout",
    "tombstones.py:write_graveyard",
    "autopsy.py:write_autopsy",
    "autopsy.py:append_actual",
    "cli.py:_log_hook_error",
    "cli.py:merge_mcp_settings",
    "cli.py:_write_if_absent",
    "cli.py:uninstall",
    "cli.py:uninstall.remove",
    "cli.py:_purge_tree",
}

# Reached only through update_json, which guards the sidecar and the target.
CALLER_GUARDED = {
    "tombstones.py:_atomic_write",
    "tombstones.py:_queue_pending",
    "tombstones.py:_drain_pending",
}

# Writes outside .kiro/tombstone by design: the install record in the user
# state dir, and the power folder generator that writes to an explicit root.
OUTSIDE = {
    "cli.py:_save_installed",
    "cli.py:write_power_folder",
}

ALLOWED = DIRECT | CALLER_GUARDED | OUTSIDE
GUARD_NAMES = {"guard", "is_safe_target"}
WRITE_ATTRS = {"write_text", "write_bytes", "unlink", "rmdir", "rmtree",
               "rename", "remove", "mkstemp"}


def _write_mode(mode) -> bool:
    if mode is None:
        return False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return any(ch in mode.value for ch in "wax+")
    return True  # a mode we cannot read statically counts as a write


def _mode_arg(call: ast.Call, position: int):
    for keyword in call.keywords:
        if keyword.arg == "mode":
            return keyword.value
    return call.args[position] if len(call.args) > position else None


def _is_write_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        if func.id == "open":
            return _write_mode(_mode_arg(call, 1))
        if func.id in ("rmtree", "mkstemp"):
            return True
        return False
    if not isinstance(func, ast.Attribute):
        return False
    owner = func.value.id if isinstance(func.value, ast.Name) else None
    if func.attr == "replace":
        return owner == "os"
    if func.attr == "remove":
        return owner == "os"
    if func.attr == "open":
        if owner == "os":
            return True  # os.open takes flags, always treated as a write
        return _write_mode(_mode_arg(call, 0))
    if func.attr == "fdopen":
        return _write_mode(_mode_arg(call, 1))
    return func.attr in WRITE_ATTRS - {"remove"}


class _Walker(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.stack: list[str] = []
        self.sites: list[tuple[str, int]] = []
        self.calls: list[tuple[str, str]] = []  # (callee name, enclosing id)
        self.guarded: set[str] = set()

    def _id(self) -> str:
        return f"{self.module}:{'.'.join(self.stack) or '<module>'}"

    def visit_FunctionDef(self, node):  # noqa: N802
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):  # noqa: N802
        if _is_write_call(node):
            self.sites.append((self._id(), node.lineno))
        func = node.func
        name = func.id if isinstance(func, ast.Name) else \
            func.attr if isinstance(func, ast.Attribute) else None
        if name:
            self.calls.append((name, self._id()))
            if name in GUARD_NAMES:
                self.guarded.add(self._id())
        self.generic_visit(node)


def _scan():
    sites, calls, guarded = [], [], set()
    for path in sorted(SRC.glob("*.py")):
        walker = _Walker(path.name)
        walker.visit(ast.parse(path.read_text(encoding="utf-8")))
        sites += walker.sites
        calls += walker.calls
        guarded |= walker.guarded
    return sites, calls, guarded


def test_every_write_site_is_in_an_allowlisted_function():
    sites, _calls, _guarded = _scan()
    assert sites, "the scanner found no write call at all, it is broken"
    bad = [f"{where} line {line}" for where, line in sites if where not in ALLOWED]
    assert not bad, "unguarded write call sites: " + ", ".join(bad)


def test_allowlist_has_no_stale_entries():
    sites, _calls, _guarded = _scan()
    found = {where for where, _line in sites}
    assert ALLOWED - found == set(), f"stale allowlist entries: {ALLOWED - found}"


def test_direct_entries_call_the_guard():
    _sites, _calls, guarded = _scan()
    missing = DIRECT - guarded
    assert not missing, f"allowlisted without a guard call: {sorted(missing)}"


def test_caller_guarded_helpers_are_only_called_from_direct_entries():
    _sites, calls, _guarded = _scan()
    helpers = {entry.split(":", 1)[1] for entry in CALLER_GUARDED}
    outside = [(name, where) for name, where in calls
               if name in helpers and where not in DIRECT
               and where not in CALLER_GUARDED]
    assert not outside, f"unguarded callers of guarded helpers: {outside}"


def test_scanner_flags_what_it_should():
    source = ("import os\n"
              "def f(p):\n"
              "    p.write_text('x')\n"
              "    open(p, 'a')\n"
              "    p.open('w')\n"
              "    os.replace(p, p)\n"
              "    p.unlink()\n"
              "    rmtree(p)\n"
              "    open(p)\n"
              "    p.open()\n"
              "    'a'.replace('a', 'b')\n")
    walker = _Walker("x.py")
    walker.visit(ast.parse(source))
    assert [line for _where, line in walker.sites] == [3, 4, 5, 6, 7, 8]
