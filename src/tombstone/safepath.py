"""Symlink-safe target check shared by init, uninstall and the state writers."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

REFUSED = "refused (symlink or outside project)"

# Paths already reported in this process, so a hook that tries several writers
# under one bad directory prints one line per path, not one per attempt.
_REPORTED: set[str] = set()


def guard(project: Path, path: Path) -> bool:
    """``is_safe_target`` for a state writer: True to write, False to skip.

    A refused write is skipped silently apart from one stderr line per path.
    Never raises, so hooks keep their exit code.
    """
    try:
        if is_safe_target(project, path):
            return True
    except (OSError, ValueError, RuntimeError):
        pass
    key = str(path)
    if key not in _REPORTED:
        _REPORTED.add(key)
        try:
            sys.stderr.write(f"tombstone: write skipped, {REFUSED} {path}\n")
            sys.stderr.flush()
        except (OSError, ValueError):
            pass
    return False


def is_safe_target(project: Path, path: Path) -> bool:
    """True when ``path`` may be written or deleted for ``project``.

    Every component from the project root down to ``path`` is checked with
    lstat and none may be a symlink (missing components are fine). The
    resolved target must also sit inside the resolved project root. The
    project root itself is the caller's already resolved directory.
    """
    root = Path(os.path.abspath(project))
    target = Path(os.path.abspath(path))
    try:
        rel = target.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in rel.parts:
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            break
        except OSError:
            return False
        if stat.S_ISLNK(mode):
            return False
    try:
        return target.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        return False
