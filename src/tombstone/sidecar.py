"""The project sidecar directory and its one-time migration (R12.1, R12.2).

All machine-local runtime state lives in ``.tombstone/`` at the project root.
Older versions kept it in ``.kiro/tombstone/``. The Kiroween rules forbid
ignoring ``/.kiro`` in git, so the state moved out of it.

This module has one job: name the directory and move an old one. Every other
module imports ``SIDECAR`` from here.
"""

from __future__ import annotations

import os
from pathlib import Path

from .safepath import guard

SIDECAR_NAME = ".tombstone"
SIDECAR = Path(SIDECAR_NAME)

# The pre-R12 location. Read by the migration only, never written to.
LEGACY = Path(".kiro") / "tombstone"


def migrate(project: Path) -> bool:
    """Move ``.kiro/tombstone/`` to ``.tombstone/`` once. True when moved.

    Does nothing when the old directory is absent, when ``.tombstone/``
    already exists (the old one is then left alone), or when any path
    component is a symlink, so a planted link is never followed. The move is
    one ``os.rename`` inside the project, which is atomic. Never raises, so a
    hook keeps its exit code.
    """
    project = Path(project)
    old = project / LEGACY
    new = project / SIDECAR
    try:
        if not os.path.lexists(old) or os.path.lexists(new):
            return False
        if not (guard(project, old) and guard(project, new)):
            return False
        if not old.is_dir():
            return False
        os.rename(old, new)
        return True
    except OSError:
        return False
