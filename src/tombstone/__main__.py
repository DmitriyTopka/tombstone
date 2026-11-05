"""Run the tombstone CLI as ``python -m tombstone``.

The detached reconcile child started by the Stop hook invokes the package this
way so it does not depend on a console-script being on PATH (Bug 1).
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
