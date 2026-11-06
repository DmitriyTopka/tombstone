"""Symlink-safe target check shared by init, uninstall and the state writers."""

from __future__ import annotations

import errno
import os
import stat
import sys
import secrets
from pathlib import Path
from typing import IO

REFUSED = "refused (symlink or outside project)"

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


def _refuse(path: Path, why: str) -> OSError:
    return OSError(errno.ELOOP, f"{REFUSED}: {why}", str(path))


def open_nofollow(path: Path, mode: str = "r",
                  encoding: str | None = None) -> IO:
    """Open ``path`` without following a symlink in its final component.

    Modes: ``r``/``r+`` (no create), ``w`` (create, truncate) and ``a``
    (create, append), each with an optional ``b``. Use ``a`` for lock files:
    it creates the file but never truncates it. The open uses ``O_NOFOLLOW``
    so a planted symlink fails with ``OSError`` before anything is read or
    written. After the open the descriptor must be a regular file, and a
    write mode also needs a link count of 1 (a hard link to a file elsewhere
    is refused). ``w`` truncates only after those checks, never at open time.
    Raises ``OSError`` on refusal, like ``open``.
    """
    binary = "b" in mode
    plus = "+" in mode
    kind = mode.replace("b", "").replace("+", "")
    if kind not in ("r", "w", "a"):
        raise ValueError(f"unsupported mode {mode!r}")
    writing = kind != "r" or plus
    if kind == "r":
        flags = os.O_RDWR if plus else os.O_RDONLY
    elif kind == "w":
        flags = os.O_RDWR if plus else os.O_WRONLY
        flags |= os.O_CREAT
    else:
        flags = (os.O_RDWR if plus else os.O_WRONLY) | os.O_CREAT | os.O_APPEND
    # O_NONBLOCK keeps a planted FIFO from hanging the hook at open time.
    fd = os.open(path, flags | _NOFOLLOW | _NONBLOCK, 0o666)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _refuse(path, "not a regular file")
        if writing and info.st_nlink > 1:
            raise _refuse(path, "hard linked file")
        if kind == "w":
            os.ftruncate(fd, 0)
        return os.fdopen(fd, mode, encoding=None if binary else (encoding or "utf-8"))
    except BaseException:
        os.close(fd)
        raise


def append_nofollow(path: Path, text: str) -> None:
    """Append ``text`` to ``path`` (created when absent), never via a symlink."""
    with open_nofollow(path, "a", encoding="utf-8") as handle:
        handle.write(text)


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a temp file and ``os.replace``.

    The temp file is created in the same directory with ``O_EXCL`` and
    ``O_NOFOLLOW``, flushed and fsynced, then renamed over the target. The
    rename replaces a directory entry and never writes through one, but a
    target that is a symlink or not a regular file is refused first so a
    planted link is reported instead of silently swapped. An existing
    target keeps its permission bits. Parent directories are created.
    Raises ``OSError`` on refusal. No temp file is left behind on failure.
    """
    path = Path(path)
    mode = 0o666
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        info = None
    if info is not None:
        if stat.S_ISLNK(info.st_mode):
            raise _refuse(path, "target is a symlink")
        if not stat.S_ISREG(info.st_mode):
            raise _refuse(path, "target is not a regular file")
        mode = stat.S_IMODE(info.st_mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

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
