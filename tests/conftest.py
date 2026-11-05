"""Shared pytest setup.

This repo runs its own Tombstone hooks, so its real ``tombstones.json`` and
``.kiro/tombstone/`` are evidence. Every test starts in an empty temp
directory so a command that looks up the project root from the working
directory (``cost``, ``graveyard``) can never reach the real one.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path_factory, monkeypatch):
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
