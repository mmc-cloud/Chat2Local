"""Personal persistence, scope isolation, safety and parameter validation."""

import asyncio
from contextlib import contextmanager

import pytest

from chat2local.dispatch.local import LocalToolDispatcher, ToolExecutionError
from chat2local.handoff import store as module
from chat2local.handoff.models import (
    HandoffNotFoundError, InvalidHandoffError, RevisionConflictError,
)
from chat2local.handoff.paths import personal_storage_directory, workspace_key
from chat2local.handoff.store import HandoffStore
from chat2local.runtime.config import AppConfig
from chat2local.runtime.workspace import WorkspaceManager
from conftest import handoff_directory, run
from test_apply_patch import create_link


def test_personal_lifecycle_isolation_and_legacy_workspace(workspace, isolated_user_data):
    store = HandoffStore()
    directory = personal_storage_directory(isolated_user_data)
    assert directory == isolated_user_data / "handoffs" / "_personal"
    assert run(store.list(None)) == {"handoffs": []}
    with pytest.raises(HandoffNotFoundError):
        run(store.get(None, "missing"))
    with pytest.raises(RevisionConflictError):
        run(store.save(None, "missing", "Title", "Summary", "body", 5))
    assert not isolated_user_data.exists()

    # An existing Workspace record is read in place, without migration.
    legacy = handoff_directory(workspace) / "research.md"
    data = ("---\nrevision: 7\nupdated_at: 2026-10-03T02:30:00Z\n"
            "title: Project\nsummary: Existing state\n---\n\nlegacy body").encode()
    legacy.write_bytes(data)
    assert run(store.get(workspace, "research"))["revision"] == 7
    created = run(store.save(None, "research", "研究 🚀", "初步想法", "notes\r\nnext", 0))
    assert run(store.list(None)) == {"handoffs": [created]}
    assert run(store.get(None, "research")) == {**created, "content": "notes\nnext"}
    updated = run(store.save(None, "research", "New title", "New summary", "latest", 1))
    assert updated["revision"] == 2
    assert run(HandoffStore().get(None, "research")) == {**updated, "content": "latest"}
    assert run(store.list(workspace))["handoffs"][0]["revision"] == 7
    assert legacy.read_bytes() == data
    assert set(directory.iterdir()) == {directory / "research.md"}
    assert all("content" not in row for row in run(store.list(None))["handoffs"])


def test_personal_does_not_select_or_depend_on_default_workspace(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    dispatchers = [LocalToolDispatcher(WorkspaceManager(root), AppConfig()) for root in (first, second)]
    def fail(*args):
        pytest.fail("Personal must not resolve any workspace")
    for dispatcher in dispatchers:
        monkeypatch.setattr(dispatcher, "_workspace", fail)
    first.rmdir()
    second.rmdir()
    saved = run(dispatchers[0].execute("handoff_save", dict(
        scope="personal", workstream="idea", title="Idea", summary="Summary", content="notes", expected_revision=0,
    )))
    assert run(dispatchers[1].execute("handoff_get", {"scope": "personal", "workstream": "idea"})) == {
        **saved, "content": "notes",
    }
    assert run(dispatchers[1].execute("handoff_list", {"scope": "personal"})) == {"handoffs": [saved]}


@pytest.mark.parametrize("tool,extra", [
    ("handoff_list", {}), ("handoff_get", {"workstream": "idea"}),
    ("handoff_save", dict(workstream="idea", title="Idea", summary="Summary", content="notes", expected_revision=0)),
])
@pytest.mark.parametrize("scope", [None, "invalid", "Personal", "", 1, True, [], {}])
def test_invalid_scope(workspace, isolated_user_data, tool, extra, scope):
    dispatcher = LocalToolDispatcher(workspace, AppConfig())
    with pytest.raises(ToolExecutionError, match="Invalid arguments.*scope"):
        run(dispatcher.execute(tool, {**extra, "scope": scope}))
    assert not isolated_user_data.exists()


@pytest.mark.parametrize("tool,extra", [
    ("handoff_list", {}), ("handoff_get", {"workstream": "idea"}),
    ("handoff_save", dict(workstream="idea", title="Idea", summary="Summary", content="notes", expected_revision=0)),
])
@pytest.mark.parametrize("selected", [None, "", "relative", "absolute"])
def test_personal_rejects_explicit_workspace(workspace, isolated_user_data, tool, extra, selected):
    dispatcher = LocalToolDispatcher(workspace, AppConfig())
    selected = str(workspace.root) if selected == "absolute" else selected
    with pytest.raises(ToolExecutionError, match="workspace must be omitted"):
        run(dispatcher.execute(tool, {**extra, "scope": "personal", "workspace": selected}))
    assert not isolated_user_data.exists()


@pytest.mark.parametrize("kind", ["symlink", "junction"])
@pytest.mark.parametrize("location", ["ancestor", "user-data", "base", "directory", "file"])
def test_personal_storage_rejects_links(tmp_path, kind, location):
    if kind == "junction" and location == "file":
        pytest.skip("Junctions are directory links")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "idea.md"
    secret.write_bytes(b"keep")
    user_data = tmp_path / "parent" / "user-data"
    linked = {
        "ancestor": user_data.parent, "user-data": user_data,
        "base": user_data / "handoffs", "directory": user_data / "handoffs" / "_personal",
        "file": user_data / "handoffs" / "_personal" / "idea.md",
    }[location]
    linked.parent.mkdir(parents=True, exist_ok=True)
    create_link(linked, secret if location == "file" else outside, kind)
    store = HandoffStore(user_data)
    for call in (lambda: store.list(None), lambda: store.get(None, "idea"),
                 lambda: store.save(None, "idea", "Title", "Summary", "new", 0)):
        with pytest.raises(InvalidHandoffError, match="symlinks or junctions"):
            run(call())
    assert list(outside.iterdir()) == [secret]
    assert secret.read_bytes() == b"keep"


def test_personal_non_directory_and_corrupt_record(isolated_user_data):
    directory = personal_storage_directory(isolated_user_data)
    directory.parent.mkdir(parents=True)
    directory.write_bytes(b"keep")
    store = HandoffStore()
    with pytest.raises(InvalidHandoffError, match="must be a directory"):
        run(store.list(None))
    directory.unlink()
    directory.mkdir()
    path = directory / "idea.md"
    path.write_bytes(b"broken")
    for call in (lambda: store.list(None), lambda: store.get(None, "idea"),
                 lambda: store.save(None, "idea", "Title", "Summary", "new", 0)):
        with pytest.raises(InvalidHandoffError):
            run(call())
    assert path.read_bytes() == b"broken"


@pytest.mark.parametrize("canonical", ["/_personal", r"C:\_personal", r"\\server\_personal"])
def test_personal_reserved_name_cannot_be_workspace_key(canonical):
    assert workspace_key(canonical) != "_personal"


@pytest.mark.parametrize("failure", ["temp", "write", "flush", "replace"])
def test_personal_atomic_failure_and_revision_conflict(isolated_user_data, monkeypatch, failure):
    store = HandoffStore()
    run(store.save(None, "idea", "Title", "Summary", "original", 0))
    path = personal_storage_directory(isolated_user_data) / "idea.md"
    original = path.read_bytes()
    with pytest.raises(RevisionConflictError):
        run(store.save(None, "idea", "Changed", "Changed", "new", 0))
    assert path.read_bytes() == original
    def fail(*args, **kwargs):
        raise PermissionError("injected failure")
    if failure == "replace":
        monkeypatch.setattr(module.os, "replace", fail)
    elif failure == "temp":
        monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", fail)
    else:
        real_temp = module.tempfile.NamedTemporaryFile
        @contextmanager
        def failing_temp(*args, **kwargs):
            with real_temp(*args, **kwargs) as handle:
                setattr(handle, failure, fail)
                yield handle
        monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", failing_temp)
    with pytest.raises(OSError, match="handoff_write_failed"):
        run(store.save(None, "idea", "Changed", "Changed", "new", 1))
    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]


def test_personal_and_workspace_concurrent_updates_have_independent_locks(workspace):
    async def scenario():
        store = HandoffStore()
        for selected in (None, workspace):
            await store.save(selected, "idea", "Title", "Summary", "original", 0)
        results = await asyncio.gather(*[
            store.save(selected, "idea", body, body, body, 1)
            for selected in (None, workspace) for body in ("A", "B")
        ], return_exceptions=True)
        for selected, pair in ((None, results[:2]), (workspace, results[2:])):
            winners = [result for result in pair if isinstance(result, dict)]
            assert len(winners) == 1 and winners[0]["revision"] == 2
            assert sum(isinstance(result, RevisionConflictError) for result in pair) == 1
            assert await store.get(selected, "idea") == {**winners[0], "content": winners[0]["title"]}
    run(scenario())
