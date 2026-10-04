"""Shared fixtures and helpers for the Tool tests.

One module per Tool lives alongside this file (``test_read.py``,
``test_search.py``, ...), so anything more than one of them needs belongs here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Coroutine

import pytest

from chat2local.handoff.paths import storage_directory
from chat2local.runtime import config
from chat2local.runtime.workspace import WorkspaceManager


def run(coroutine: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coroutine)


@pytest.fixture(autouse=True)
def isolated_user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every dispatcher/store uses temporary data, never the developer's home."""
    directory = tmp_path / "user-data"
    monkeypatch.setattr(config, "user_data_directory", lambda: directory)
    return directory


def handoff_directory(workspace: WorkspaceManager | Path) -> Path:
    root = workspace.root if isinstance(workspace, WorkspaceManager) else workspace.resolve()
    return storage_directory(root, config.user_data_directory(), create=True)


@pytest.fixture(autouse=True)
def reset_device_router(monkeypatch: pytest.MonkeyPatch) -> None:
    # CLI startup binds a router; other in-process tests bind their own runtime.
    monkeypatch.setattr("chat2local.mcp.tools._router", None)


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceManager:
    root = tmp_path / "workspace"
    root.mkdir()

    return WorkspaceManager(root)


def make_file(
    workspace: WorkspaceManager, relative: str, text: str, encoding: str = "utf-8"
) -> Path:
    path = workspace.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)

    # newline="" keeps the written bytes identical on every platform.
    with path.open("w", encoding=encoding, newline="") as handle:
        handle.write(text)

    return path
