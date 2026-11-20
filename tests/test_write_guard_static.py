"""Static check: every file write or delete in src/ goes through safepath.

Walks the AST of ``src/tombstone/*.py``.

1. A plain ``open(``, ``.open(``, ``os.open``, ``fdopen``, ``write_text``,
   ``write_bytes``, ``os.replace`` or ``mkstemp`` call fails unless it sits in
   ``safepath.py`` (the helpers) or in the PLAIN_ALLOWED list (read-only
   ``/proc`` lookups).
2. Delete calls (``unlink``, ``rmdir``, ``rmtree``, ``rename``, ``os.remove``)
   must sit in a DIRECT function that itself calls ``guard`` or
   ``is_safe_target``.
3. A function that calls ``open_nofollow``, ``append_nofollow`` or
   ``write_atomic`` must also call the guard (parents are checked by the
   guard, the last component by the helper), unless it is listed in
   HELPER_UNGUARDED.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "tombstone"

HELPER_MODULE = "safepath.py"

# Read-only opens that cannot write: the Linux /proc process lookup.
PLAIN_ALLOWED = {"stop.py:_list_processes"}

# Delete sites. Each must call guard or is_safe_target itself.
DIRECT = {
    "stop.py:resume",
    "tombstones.py:_drain_pending",
    "cli.py:uninstall",
    "cli.py:uninstall.remove",
    "cli.py:_purge_tree",
    "sidecar.py:migrate",
}

# Helper callers with no project to guard: the install record sits outside
# the project on purpose, the power generator writes to an explicit root, and
# _atomic_write is reached only through update_json (checked below).
HELPER_UNGUARDED = {
    "cli.py:_save_installed",
    "cli.py:write_power_folder",
    "autopsy.py:_read_one",
    "tombstones.py:_atomic_write",
}
CALLER_GUARDED = {"tombstones.py:_atomic_write"}

GUARD_NAMES = {"guard", "is_safe_target"}
HELPER_NAMES = {"open_nofollow", "append_nofollow", "write_atomic"}
DELETE_ATTRS = {"unlink", "rmdir", "rmtree", "rename"}
PLAIN_ATTRS = {"write_text", "write_bytes", "mkstemp", "fdopen", "open"}


def _kind(call: ast.Call) -> str | None:
    """"plain" for a raw write primitive, "delete" for a delete, else None."""
    func = call.func
    if isinstance(func, ast.Name):
        if func.id in ("open", "mkstemp", "fdopen"):
            return "plain"
        if func.id == "rmtree":
            return "delete"
        return None
    if not isinstance(func, ast.Attribute):
        return None
    owner = func.value.id if isinstance(func.value, ast.Name) else None
    if func.attr == "replace":
        return "plain" if owner == "os" else None
    if func.attr == "remove":
        return "delete" if owner == "os" else None
    if func.attr in PLAIN_ATTRS:
        return "plain"
    if func.attr in DELETE_ATTRS:
        return "delete"
    return None


class _Walker(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.stack: list[str] = []
        self.plain: list[tuple[str, int]] = []
        self.deletes: list[tuple[str, int]] = []
        self.helper_users: set[str] = set()
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
        kind = _kind(node)
        if kind == "plain":
            self.plain.append((self._id(), node.lineno))
        elif kind == "delete":
            self.deletes.append((self._id(), node.lineno))
        func = node.func
        name = func.id if isinstance(func, ast.Name) else \
            func.attr if isinstance(func, ast.Attribute) else None
        if name:
            self.calls.append((name, self._id()))
            if name in GUARD_NAMES:
                self.guarded.add(self._id())
            if name in HELPER_NAMES:
                self.helper_users.add(self._id())
        self.generic_visit(node)


def _scan():
    plain, deletes, calls = [], [], []
    guarded: set[str] = set()
    users: set[str] = set()
    for path in sorted(SRC.glob("*.py")):
        walker = _Walker(path.name)
        walker.visit(ast.parse(path.read_text(encoding="utf-8")))
        if path.name != HELPER_MODULE:
            plain += walker.plain
            deletes += walker.deletes
            users |= walker.helper_users
        calls += walker.calls
        guarded |= walker.guarded
    return plain, deletes, calls, guarded, users


def test_no_plain_open_or_write_outside_safepath():
    plain, _deletes, _calls, _guarded, _users = _scan()
    bad = [f"{where} line {line}" for where, line in plain
           if where not in PLAIN_ALLOWED]
    assert not bad, "plain open or write call sites (use safepath): " + ", ".join(bad)


def test_plain_allowlist_has_no_stale_entries():
    plain, _deletes, _calls, _guarded, _users = _scan()
    found = {where for where, _line in plain}
    assert PLAIN_ALLOWED - found == set(), f"stale: {PLAIN_ALLOWED - found}"


def test_every_delete_site_is_a_direct_guarded_function():
    _plain, deletes, _calls, guarded, _users = _scan()
    bad = [f"{where} line {line}" for where, line in deletes if where not in DIRECT]
    assert not bad, "unguarded delete call sites: " + ", ".join(bad)
    found = {where for where, _line in deletes}
    assert DIRECT - found == set(), f"stale DIRECT entries: {DIRECT - found}"
    missing = DIRECT - guarded
    assert not missing, f"allowlisted without a guard call: {sorted(missing)}"


def test_helper_callers_call_the_guard_or_are_listed():
    _plain, _deletes, _calls, guarded, users = _scan()
    missing = users - guarded - HELPER_UNGUARDED
    assert not missing, f"safepath helper used without a guard call: {sorted(missing)}"
    stale = HELPER_UNGUARDED - users
    assert not stale, f"stale HELPER_UNGUARDED entries: {sorted(stale)}"


def test_caller_guarded_helpers_are_only_called_from_guarded_functions():
    _plain, _deletes, calls, guarded, _users = _scan()
    helpers = {entry.split(":", 1)[1] for entry in CALLER_GUARDED}
    outside = [(name, where) for name, where in calls
               if name in helpers and where not in guarded
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
              "    'a'.replace('a', 'b')\n"
              "    write_atomic(p, 'x')\n")
    walker = _Walker("x.py")
    walker.visit(ast.parse(source))
    assert [line for _where, line in walker.plain] == [3, 4, 5, 6, 9, 10]
    assert [line for _where, line in walker.deletes] == [7, 8]
    assert walker.helper_users == {"x.py:f"}
