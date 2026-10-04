"""Latest-state persistence, corrupt files, atomic failures and real contention."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import threading

import pytest

from chat2local.handoff import store as module
from chat2local.handoff.models import (
    HandoffNotFoundError, InvalidHandoffError, InvalidWorkstreamError, RevisionConflictError,
)
from chat2local.handoff.store import HandoffStore, validate_workstream
from chat2local.runtime.workspace import WorkspaceError, WorkspaceManager
from conftest import handoff_directory, run
from test_apply_patch import create_link

INVALID_DISCOVERY = [
    (field, value)
    for field, maximum in (("title", 120), ("summary", 500))
    for value in ("", "   ", "\u3000", "x" * (maximum + 1), "a\nb", "a\rb", 1, True, None)
]


@pytest.mark.parametrize("slug", ["stage4-design", "hub_deployment", "A1-test", "a" * 80])
def test_valid_workstream(slug):
    assert validate_workstream(slug) == slug


@pytest.mark.parametrize("slug", ["../x", "a/b", "a\\b", "a b", "", "a" * 81, "-a", "_a", "a\n", "中文", "C:\\foo", 1])
def test_invalid_workstream_at_store_boundary(workspace, slug):
    store = HandoffStore()
    with pytest.raises(InvalidWorkstreamError, match="^invalid_workstream:"):
        run(store.get(workspace, slug))
    with pytest.raises(InvalidWorkstreamError, match="^invalid_workstream:"):
        run(store.save(workspace, slug, "Title", "Summary", "body", 0))
    assert not (workspace.root / "HANDOFFS").exists()


def test_create_get_update_and_managed_metadata(workspace):
    store = HandoffStore()
    legacy = workspace.root / "HANDOFF.md"
    legacy.write_bytes(b"keep experimental handoff")

    async def scenario():
        assert await store.list(workspace) == {"handoffs": []}
        assert not (workspace.root / "HANDOFFS").exists()
        body = "---\r\nrevision: 999\r\n---\r\n正文😀\rnext"
        created = await store.save(workspace, "design", "阶段四：跨会话续接 🚀", "正在验证新的 Chat 是否可以直接继续工作。", body, 0)
        assert set(created) == {"workstream", "revision", "updated_at", "title", "summary"}
        assert created["revision"] == 1
        assert created["title"] == "阶段四：跨会话续接 🚀"
        assert created["summary"] == "正在验证新的 Chat 是否可以直接继续工作。"
        assert datetime.fromisoformat(created["updated_at"]).utcoffset().total_seconds() == 0
        assert datetime.fromisoformat(created["updated_at"]) <= datetime.now(timezone.utc)
        assert await store.get(workspace, "design") == {
            **created, "content": body.replace("\r\n", "\n").replace("\r", "\n"),
        }
        updated = await store.save(workspace, "design", "New title", "New summary", "latest", 1)
        assert updated["revision"] == 2
        assert updated["title"] == "New title" and updated["summary"] == "New summary"
        assert (await store.get(workspace, "design"))["content"] == "latest"
        assert await store.list(workspace) == {"handoffs": [updated]}
    run(scenario())
    data = (handoff_directory(workspace) / "design.md").read_bytes()
    assert data.startswith(b"---\nrevision: 2\nupdated_at: ") and b"\r" not in data
    assert not data.startswith(b"\xef\xbb\xbf") and data.endswith(b"\n\nlatest")
    assert legacy.read_bytes() == b"keep experimental handoff"


@pytest.mark.parametrize("expected", [0, 1, 5])
def test_conflict_preserves_exact_file(workspace, expected):
    store = HandoffStore()
    run(store.save(workspace, "design", "Title", "Summary", "one", 0))
    run(store.save(workspace, "design", "Title", "Summary", "two", 1))
    path = handoff_directory(workspace) / "design.md"
    before = path.read_bytes()
    with pytest.raises(RevisionConflictError, match=f"expected revision {expected}, current revision 2"):
        run(store.save(workspace, "design", "Title", "Summary", "must not write", expected))
    assert path.read_bytes() == before
    assert set(path.parent.iterdir()) == {path, path.parent / "workspace.json"}


def test_missing_and_missing_revision_conflict(workspace):
    store = HandoffStore()
    with pytest.raises(HandoffNotFoundError, match="^handoff_not_found: missing"):
        run(store.get(workspace, "missing"))
    with pytest.raises(RevisionConflictError, match="current revision 0"):
        run(store.save(workspace, "missing", "Title", "Summary", "body", 5))
    assert not (workspace.root / "HANDOFFS").exists()


@pytest.mark.parametrize("expected", [True, "0", 0.0, -1, None])
def test_store_revision_is_strict(workspace, expected):
    with pytest.raises(ValueError, match="strict integer"):
        run(HandoffStore().save(workspace, "design", "Title", "Summary", "body", expected))
    assert not (workspace.root / "HANDOFFS").exists()


def test_list_sorted_metadata_only_and_no_recursion(workspace):
    store = HandoffStore()
    for slug in ("z", "a", "B"):
        run(store.save(workspace, slug, "Title", "Summary", "secret body", 0))
    directory = handoff_directory(workspace)
    (directory / "nested.md").mkdir()
    (directory / "nested.md/invalid.md").write_bytes(b"broken")
    (directory / "ignored.txt").write_bytes(b"broken")
    listing = run(store.list(workspace))["handoffs"]
    assert [entry["workstream"] for entry in listing] == ["B", "a", "z"]
    assert all(set(entry) == {"workstream", "revision", "updated_at", "title", "summary"} for entry in listing)
    # New store reads metadata from disk, with no in-memory business state.
    assert run(HandoffStore().list(workspace))["handoffs"] == listing


@pytest.mark.parametrize("data", [
    b"body without front matter",
    b"---\nupdated_at: 2026-10-03T02:30:00Z\n---\n\nbody",
    *[f"---\nrevision: {revision}\nupdated_at: 2026-10-03T02:30:00Z\n---\n\nbody".encode()
      for revision in ("0", "-1", "true", "1.5", "bad")],
    *[f"---\nrevision: 1\nupdated_at: {stamp}\n---\n\nbody".encode()
      for stamp in ("bad", "2026-99-03T02:30:00Z", "2026-10-03", "2026-10-03T02:30:00", "2026-10-03T02:30:00+08:00")],
    b"\xff",
])
def test_malformed_list_get_save_fail_without_overwrite(workspace, data):
    path = handoff_directory(workspace) / "design.md"
    path.write_bytes(data)
    store = HandoffStore()
    for call in (lambda: store.list(workspace), lambda: store.get(workspace, "design"),
                 lambda: store.save(workspace, "design", "Title", "Summary", "new", 0)):
        with pytest.raises(InvalidHandoffError, match="^invalid_handoff:"):
            run(call())
        assert path.read_bytes() == data


@pytest.mark.parametrize("failure", ["temp", "write", "flush", "replace"])
def test_atomic_write_failure_preserves_original_and_cleans_temp(workspace, monkeypatch, failure):
    store = HandoffStore()
    run(store.save(workspace, "design", "Title", "Summary", "original", 0))
    path = handoff_directory(workspace) / "design.md"
    original = path.read_bytes()
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
    with pytest.raises(OSError, match="^handoff_write_failed:"):
        run(store.save(workspace, "design", "Changed title", "Changed summary", "changed", 1))
    assert path.read_bytes() == original
    assert run(store.get(workspace, "design"))["title"] == "Title"
    assert run(store.get(workspace, "design"))["summary"] == "Summary"
    assert run(store.get(workspace, "design"))["content"] == "original"
    assert set(path.parent.iterdir()) == {path, path.parent / "workspace.json"}


def test_concurrent_save_has_exactly_one_winner(workspace):
    async def scenario():
        store = HandoffStore()
        await store.save(workspace, "design", "Title", "Summary", "original", 0)
        results = await asyncio.gather(
            store.save(workspace, "design", "Title A", "Summary A", "A", 1),
            store.save(workspace, "design", "Title B", "Summary B", "B", 1), return_exceptions=True,
        )
        winners = [index for index, result in enumerate(results) if isinstance(result, dict)]
        assert len(winners) == 1 and results[winners[0]]["revision"] == 2
        assert isinstance(results[1 - winners[0]], RevisionConflictError)
        winner = ("A", "B")[winners[0]]
        assert await store.get(workspace, "design") == {
            **results[winners[0]], "content": winner,
        }
        assert results[winners[0]]["title"] == f"Title {winner}"
        assert results[winners[0]]["summary"] == f"Summary {winner}"
    run(scenario())


def test_cancelled_save_keeps_lock_until_worker_finishes(workspace, monkeypatch):
    store = HandoffStore()
    run(store.save(workspace, "design", "Title", "Summary", "original", 0))
    entered, release = threading.Event(), threading.Event()
    real_replace = module.os.replace
    def delayed_replace(*args):
        entered.set()
        assert release.wait(5), "write worker was not released"
        real_replace(*args)
    monkeypatch.setattr(module.os, "replace", delayed_replace)

    async def scenario():
        first = asyncio.create_task(store.save(workspace, "design", "Title A", "Summary A", "A", 1))
        second = None
        try:
            async with asyncio.timeout(3):
                while not entered.is_set():
                    await asyncio.sleep(0.01)
            first.cancel()
            second = asyncio.create_task(store.save(workspace, "design", "Title B", "Summary B", "B", 1))
            await asyncio.sleep(0.03)
            assert not first.done() and not second.done()
            release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
            assert isinstance(results[0], asyncio.CancelledError)
            assert isinstance(results[1], RevisionConflictError)
            assert (await store.get(workspace, "design"))["content"] == "A"
            assert (await store.get(workspace, "design"))["title"] == "Title A"
            assert (await store.get(workspace, "design"))["summary"] == "Summary A"
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
    run(scenario())


@pytest.mark.parametrize("kind", ["symlink", "junction"])
@pytest.mark.parametrize("location", ["directory", "file"])
def test_handoff_links_cannot_escape_workspace(workspace, tmp_path, kind, location):
    if kind == "junction" and location == "file":
        pytest.skip("Junctions are directory links")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "design.md"
    secret.write_bytes(b"keep")
    directory = workspace.root / "HANDOFFS"
    if location == "directory":
        create_link(directory, outside, kind)
    else:
        directory.mkdir()
        create_link(directory / "design.md", secret, kind)
    store = HandoffStore()
    for call in (lambda: store.list(workspace), lambda: store.get(workspace, "design"),
                 lambda: store.save(workspace, "design", "Title", "Summary", "new", 0)):
        with pytest.raises(WorkspaceError, match="outside workspace"):
            run(call())
    assert secret.read_bytes() == b"keep"


def test_in_workspace_alias_cannot_turn_handoff_into_arbitrary_file(workspace):
    target = workspace.root / "target.md"
    target.write_bytes(b"keep")
    directory = workspace.root / "HANDOFFS"
    directory.mkdir()
    create_link(directory / "design.md", target, "symlink")
    with pytest.raises(InvalidHandoffError, match="symlinks"):
        run(HandoffStore().save(workspace, "design", "Title", "Summary", "new", 0))
    assert target.read_bytes() == b"keep"


@pytest.mark.parametrize("field,value", INVALID_DISCOVERY)
def test_store_rejects_invalid_discovery_metadata_without_writing(workspace, field, value):
    arguments = dict(workspace=workspace, workstream="design", title="Title", summary="Summary",
                     content="body", expected_revision=0)
    arguments[field] = value
    with pytest.raises(ValueError, match=field):
        run(HandoffStore().save(**arguments))
    assert not (workspace.root / "HANDOFFS").exists()


@pytest.mark.parametrize("title,summary", [
    ("Stage 4: Handoff", "State: implemented; next: real usage validation."),
    ("  标题 🚀  ", "  摘要：保留首尾空格  "),
    ("a", "b"),
    ("中" * 120, "🚀" * 500),
])
def test_discovery_preserves_exact_strings_and_length_boundaries(workspace, title, summary):
    store = HandoffStore()
    saved = run(store.save(workspace, "design", title, summary, "body", 0))
    assert saved["title"] == title and saved["summary"] == summary
    assert run(store.get(workspace, "design")) == {**saved, "content": "body"}
    assert run(store.list(workspace)) == {"handoffs": [saved]}
    assert f"title: {title}\nsummary: {summary}\n" in (handoff_directory(workspace) / "design.md").read_text(encoding="utf-8")


def test_legacy_read_and_lazy_upgrade(workspace):
    store = HandoffStore()
    path = workspace.root / "HANDOFFS/legacy.md"
    path.parent.mkdir()
    old = b"---\nrevision: 1\nupdated_at: 2026-10-03T02:30:00Z\n---\n\nlegacy body"
    path.write_bytes(old)
    metadata = dict(workstream="legacy", revision=1, updated_at="2026-10-03T02:30:00Z", title=None, summary=None)
    assert run(store.list(workspace)) == {"handoffs": [metadata]}
    assert run(store.get(workspace, "legacy")) == {**metadata, "content": "legacy body"}
    assert not path.parent.exists()
    path = handoff_directory(workspace) / "legacy.md"
    assert path.read_bytes() == old
    upgraded = run(store.save(workspace, "legacy", "Legacy title", "Now upgraded", "new body", 1))
    assert upgraded["revision"] == 2
    assert run(store.get(workspace, "legacy")) == {**upgraded, "content": "new body"}
    assert b"title: Legacy title\nsummary: Now upgraded\n" in path.read_bytes()


@pytest.mark.parametrize("fields", [
    "title: Title\n", "summary: Summary\n",
    "summary: Summary\ntitle: Title\n",
    *[f"title: {value}\nsummary: Summary\n" for value in ("", "   ", "a" * 121, "a\nb", "a\rb")],
    *[f"title: Title\nsummary: {value}\n" for value in ("", "   ", "a" * 501, "a\nb", "a\rb")],
])
def test_invalid_or_half_upgraded_metadata_is_not_repaired(workspace, fields):
    path = handoff_directory(workspace) / "design.md"
    data = f"---\nrevision: 1\nupdated_at: 2026-10-03T02:30:00Z\n{fields}---\n\nbody".encode()
    path.write_bytes(data)
    store = HandoffStore()
    for call in (lambda: store.list(workspace), lambda: store.get(workspace, "design"),
                 lambda: store.save(workspace, "design", "Title", "Summary", "body", 1)):
        with pytest.raises(InvalidHandoffError, match="^invalid_handoff:"):
            run(call())
    assert path.read_bytes() == data
