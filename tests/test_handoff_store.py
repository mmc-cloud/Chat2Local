"""Latest-state persistence, corrupt files, atomic failures and real contention."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import os
import threading
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from chat2local.handoff import store as module
from chat2local.handoff.models import (
    HandoffNotFoundError, InvalidHandoffError, InvalidWorkstreamError, RevisionConflictError,
)
from chat2local.handoff.store import HandoffStore, validate_workstream
from conftest import HIDDEN_SUBPROCESS_OPTIONS, handoff_directory, run


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("personal", [False, True])
def test_independent_stores_serialize_revision_check_and_replace(workspace, monkeypatch, local, personal):
    workspace = None if personal else workspace
    first, second = HandoffStore(), HandoffStore()
    run(first.save(workspace, "design", "Title", "Summary", "original", 0))
    handoff_id = run(first.list_local())["handoffs"][0]["id"]
    entered, release = threading.Event(), threading.Event()
    second_started = threading.Event()
    original_replace = module.os.replace
    def replace(source, destination):
        # Hold A after its last revision check, before atomic replacement.
        if b"\n\nA" in Path(source).read_bytes():
            entered.set()
            assert release.wait(5)
        original_replace(source, destination)
    monkeypatch.setattr(module.os, "replace", replace)
    original_to_thread = asyncio.to_thread
    async def to_thread(function, *args, **kwargs):
        def worker():
            # Only the second task uses this patched dispatcher (installed
            # after the first has entered replace).
            second_started.set()
            return function(*args, **kwargs)
        return await original_to_thread(worker)
    async def scenario():
        a = asyncio.create_task(first.save(workspace, "design", "A", "Summary", "A", 1))
        b = None
        try:
            assert await original_to_thread(entered.wait, 3)
            monkeypatch.setattr(module.asyncio, "to_thread", to_thread)
            b = asyncio.create_task(
                second.save_local(handoff_id, "B", "Summary", "B", 1) if local else
                second.save(workspace, "design", "B", "Summary", "B", 1)
            )
            assert await original_to_thread(second_started.wait, 3)
            # Old code completes B while A still holds its prepared revision.
            done, _ = await asyncio.wait([b], timeout=0.1)
            assert not done, "A different Store bypassed the in-flight save"
            release.set()
            results = await asyncio.gather(a, b, return_exceptions=True)
            assert results[0]["revision"] == 2
            assert isinstance(results[1], RevisionConflictError)
            assert (await second.get(workspace, "design"))["content"] == "A"
        finally:
            release.set()
            await asyncio.gather(a, *([b] if b else []), return_exceptions=True)
    run(scenario())


_PROCESS_SAVE = """
import asyncio, json, os, sys, time
from pathlib import Path
from chat2local.handoff import store as module
from chat2local.handoff.models import RevisionConflictError
from chat2local.runtime.workspace import WorkspaceManager
user_data, root, signals, role, crash = sys.argv[1:]
signals = Path(signals)
entered = signals / (role + '-entered')
original_replace = module.os.replace
def replace(source, destination):
    entered.touch()
    if role == 'first':
        deadline = time.monotonic() + 10
        while not (signals / 'release').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('parent did not release writer')
            time.sleep(0.01)
        if crash == 'True':
            os._exit(17)
    original_replace(source, destination)
module.os.replace = replace
if role == 'second':
    entered.touch()
workspace = WorkspaceManager(root) if root else None
try:
    saved = asyncio.run(module.HandoffStore(Path(user_data)).save(
        workspace, 'design', 'Title', 'Summary', str(os.getpid()), 1))
    print(json.dumps(['ok', saved['revision']]))
except RevisionConflictError:
    print(json.dumps(['conflict', None]))
"""


def _writer(user_data, workspace, signals, role, *, crash=False):
    return subprocess.Popen(
        [sys.executable, "-c", _PROCESS_SAVE, str(user_data), str(workspace.root) if workspace else "",
         str(signals), role, str(crash)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **HIDDEN_SUBPROCESS_OPTIONS,
    )


def _wait_entered(child, path):
    deadline = time.monotonic() + 5
    while not path.exists():
        assert child.poll() is None, child.communicate(timeout=1)
        assert time.monotonic() < deadline, "Writer did not reach the controlled boundary"
        time.sleep(0.01)


@pytest.mark.parametrize("personal", [False, True])
def test_separate_processes_cannot_both_save_same_revision(workspace, isolated_user_data, tmp_path, personal):
    selected = None if personal else workspace
    run(HandoffStore().save(selected, "design", "Title", "Summary", "original", 0))
    release = tmp_path / "release"
    children = []
    try:
        first = _writer(isolated_user_data, selected, tmp_path, "first")
        children.append(first)
        _wait_entered(first, tmp_path / "first-entered")
        second = _writer(isolated_user_data, selected, tmp_path, "second")
        children.append(second)
        _wait_entered(second, tmp_path / "second-entered")
        with pytest.raises(subprocess.TimeoutExpired):
            second.wait(timeout=0.2)
        release.touch()
        results = []
        for child in children:
            output, errors = child.communicate(timeout=10)
            assert child.returncode == 0, errors.decode(errors="replace")
            results.append(json.loads(output))
        assert sorted(results) == [["conflict", None], ["ok", 2]]
        assert run(HandoffStore().get(selected, "design"))["revision"] == 2
    finally:
        release.touch()
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=5)


def test_crashed_writer_releases_os_lock(workspace, isolated_user_data, tmp_path):
    store = HandoffStore()
    run(store.save(workspace, "design", "Title", "Summary", "original", 0))
    child = _writer(isolated_user_data, workspace, tmp_path, "first", crash=True)
    try:
        _wait_entered(child, tmp_path / "first-entered")
        (tmp_path / "release").touch()
        child.communicate(timeout=5)
        assert child.returncode == 17
        assert run(HandoffStore().save(workspace, "design", "Title", "Summary", "retry", 1))["revision"] == 2
    finally:
        if child.poll() is None:
            child.terminate()
        child.communicate(timeout=5)


def test_failed_save_releases_lock_for_other_store(workspace, monkeypatch):
    first, second = HandoffStore(), HandoffStore()
    run(first.save(workspace, "design", "Title", "Summary", "original", 0))
    with monkeypatch.context() as patcher:
        def fail(*args):
            raise PermissionError("injected replace failure")
        patcher.setattr(module.os, "replace", fail)
        with pytest.raises(OSError, match="handoff_write_failed"):
            run(first.save(workspace, "design", "Title", "Summary", "failed", 1))
    async def scenario():
        saved = await asyncio.wait_for(second.save(workspace, "design", "Title", "Summary", "retry", 1), 3)
        assert saved["revision"] == 2
    run(scenario())

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
    assert list(workspace.root.iterdir()) == []


def test_create_get_update_and_managed_metadata(workspace):
    store = HandoffStore()

    async def scenario():
        assert await store.list(workspace) == {"handoffs": []}
        assert list(workspace.root.iterdir()) == []
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
    assert list(workspace.root.iterdir()) == []


@pytest.mark.parametrize("expected", [True, "0", 0.0, -1, None])
def test_store_revision_is_strict(workspace, expected):
    with pytest.raises(ValueError, match="strict integer"):
        run(HandoffStore().save(workspace, "design", "Title", "Summary", "body", expected))
    assert list(workspace.root.iterdir()) == []


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
    *[f"---\nrevision: {revision}\nupdated_at: 2026-10-03T02:30:00Z\ntitle: Title\nsummary: Summary\n---\n\nbody".encode()
      for revision in ("0", "-1", "true", "1.5", "bad")],
    *[f"---\nrevision: 1\nupdated_at: {stamp}\ntitle: Title\nsummary: Summary\n---\n\nbody".encode()
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


@pytest.mark.parametrize("personal", [False, True])
@pytest.mark.parametrize("different_store", [False, True])
@pytest.mark.parametrize("second_workstream", [
    "design", pytest.param("DESIGN", marks=pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive paths")),
])
def test_cancelled_save_keeps_lock_until_worker_finishes(workspace, monkeypatch, personal, second_workstream, different_store):
    workspace = None if personal else workspace
    store = HandoffStore()
    other = HandoffStore() if different_store else store
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
            second = asyncio.create_task(other.save(workspace, second_workstream, "Title B", "Summary B", "B", 1))
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


@pytest.mark.parametrize("field,value", INVALID_DISCOVERY)
def test_store_rejects_invalid_discovery_metadata_without_writing(workspace, field, value):
    arguments = dict(workspace=workspace, workstream="design", title="Title", summary="Summary",
                     content="body", expected_revision=0)
    arguments[field] = value
    with pytest.raises(ValueError, match=field):
        run(HandoffStore().save(**arguments))
    assert list(workspace.root.iterdir()) == []


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


@pytest.mark.parametrize("fields", [
    "",
    "title: Title\n", "summary: Summary\n",
    "summary: Summary\ntitle: Title\n",
    *[f"title: {value}\nsummary: Summary\n" for value in ("", "   ", "a" * 121, "a\nb", "a\rb")],
    *[f"title: Title\nsummary: {value}\n" for value in ("", "   ", "a" * 501, "a\nb", "a\rb")],
])
def test_missing_or_invalid_discovery_metadata_is_not_repaired(workspace, fields):
    path = handoff_directory(workspace) / "design.md"
    data = f"---\nrevision: 1\nupdated_at: 2026-10-03T02:30:00Z\n{fields}---\n\nbody".encode()
    path.write_bytes(data)
    store = HandoffStore()
    for call in (lambda: store.list(workspace), lambda: store.get(workspace, "design"),
                 lambda: store.save(workspace, "design", "Title", "Summary", "body", 1)):
        with pytest.raises(InvalidHandoffError, match="^invalid_handoff:"):
            run(call())
    assert path.read_bytes() == data
