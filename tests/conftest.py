"""Shared pytest setup.

This repo runs its own Tombstone hooks, so its real ``tombstones.json`` and
``.tombstone/`` are evidence. Every test starts in an empty temp
directory so a command that looks up the project root from the working
directory (``cost``, ``graveyard``) can never reach the real one.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path_factory, monkeypatch):
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path_factory, monkeypatch):
    """Install records go to a temp XDG state dir, never the real one."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("xdg_state")))


@pytest.fixture(autouse=True)
def _short_reconcile_timeout(monkeypatch):
    """Any reconcile child a test really spawns gives up after 1 second.

    Without this, spawn tests left ``tombstone hook reconcile`` children
    polling for up to 30 s after the suite ended (LESSONS.md, 2026-11-06).
    """
    from tombstone import tombstones

    original = tombstones.spawn_reconcile

    def short(project, session_id, turn_id, kiro_home=None, timeout_s=None):
        return original(project, session_id, turn_id, kiro_home=kiro_home,
                        timeout_s=1 if timeout_s is None else timeout_s)

    monkeypatch.setattr(tombstones, "spawn_reconcile", short)


@pytest.fixture(autouse=True)
def _no_real_kiro_cli_probe(monkeypatch):
    """Tests never run ``kiro-cli --version``; the tested version is reported."""
    from tombstone import watcher

    monkeypatch.setattr(watcher, "installed_cli_version",
                        lambda: watcher.TESTED_CLI_VERSION)
