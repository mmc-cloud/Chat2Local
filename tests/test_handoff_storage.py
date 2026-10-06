"""Workspace identities, ownership, publication and storage safety."""

import asyncio
import hashlib
import json
import os
import threading
from pathlib import Path

import pytest

from chat2local.handoff import paths
from chat2local.handoff.models import InvalidHandoffError
from chat2local.handoff.paths import MAX_WORKSPACE_KEY_BYTES, storage_directory, workspace_key
from chat2local.handoff.store import HandoffStore
from chat2local.runtime import config
from chat2local.runtime.config import user_data_directory
from chat2local.runtime.workspace import WorkspaceManager
from conftest import handoff_directory, run
from test_apply_patch import create_link


@pytest.mark.parametrize("canonical,key", [
    (r"D:\Projects\Chat2Local", "D--Projects-Chat2Local"),
    ("/home/alice/项目/chat2local", "-home-alice-项目-chat2local"),
    (r"\\server\share\Project", "--server-share-Project"),
])
def test_readable_keys_for_windows_and_posix(canonical, key):
    assert workspace_key(canonical) == key
    assert workspace_key(canonical) == workspace_key(canonical)


@pytest.mark.parametrize("segment", ["ascii-project", "中文项目", "🚀😀项目"])
def test_long_key_has_readable_prefix_and_deterministic_hash(segment):
    canonical = "/projects/" + f"{segment}/" * 80
    key = workspace_key(canonical)
    assert len(key.encode("utf-8")) <= MAX_WORKSPACE_KEY_BYTES
    assert len(key.encode("utf-8")) < 255
    assert key.startswith(f"-projects-{segment}-")
    prefix = key.rsplit("-", 1)[0]
    readable = canonical.replace("/", "-")
    assert readable.startswith(prefix)
    assert "\ufffd" not in key
    assert key.encode("utf-8").decode("utf-8") == key
    assert key.endswith("-" + hashlib.sha256(os.path.normcase(canonical).encode()).hexdigest()[:8])
    assert workspace_key(canonical) == key
    assert workspace_key(canonical + "other") != key


@pytest.mark.parametrize("segment", ["a", "中", "😀"])
def test_key_at_byte_limit_is_preserved_and_next_character_is_hashed(segment):
    canonical = "/" + segment * ((MAX_WORKSPACE_KEY_BYTES - 1) // len(segment.encode("utf-8")))
    canonical += "a" * (MAX_WORKSPACE_KEY_BYTES - len(canonical.encode("utf-8")))
    assert len(canonical.encode("utf-8")) == MAX_WORKSPACE_KEY_BYTES
    assert workspace_key(canonical) == canonical.replace("/", "-")
    longer = canonical + segment
    key = workspace_key(longer)
    assert len(key.encode("utf-8")) <= MAX_WORKSPACE_KEY_BYTES
    assert key.endswith("-" + hashlib.sha256(os.path.normcase(longer).encode()).hexdigest()[:8])
    assert workspace_key(longer) == key


@pytest.mark.parametrize("segment", ["ascii-project", "中文项目", "🚀😀项目"])
def test_collision_suffix_respects_byte_limit(segment):
    canonical = "/projects/" + f"{segment}/" * 80
    key = workspace_key(canonical)
    fallback = paths._with_hash(key, canonical)
    assert len(fallback.encode("utf-8")) <= MAX_WORKSPACE_KEY_BYTES
    assert fallback.startswith(f"-projects-{segment}-")
    assert fallback.endswith("-" + hashlib.sha256(os.path.normcase(canonical).encode()).hexdigest()[:8])
    assert paths._with_hash(key, canonical) == fallback


def test_shared_user_data_helper_uses_existing_home_rule(tmp_path, monkeypatch):
    assert config.DEFAULT_CONFIG_PATH == Path.home() / ".chat2local" / "config.yaml"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert user_data_directory() == tmp_path / ".chat2local"
    assert HandoffStore()._user_data == config.user_data_directory()


def test_repeat_resolution_ownership_and_normalization(workspace, isolated_user_data):
    directory = handoff_directory(workspace)
    canonical = str(workspace.root)
    assert directory == isolated_user_data / "handoffs" / workspace_key(canonical)
    assert not directory.is_relative_to(workspace.root)
    assert storage_directory(workspace.root, isolated_user_data) == directory
    assert storage_directory(workspace.root, isolated_user_data, create=True) == directory
    alias = WorkspaceManager(workspace.root / ".")
    assert storage_directory(alias.root, isolated_user_data) == directory
    assert json.loads((directory / "workspace.json").read_text(encoding="utf-8")) == {
        "canonical_path": canonical, "display_name": workspace.root.name,
    }
    run(HandoffStore().save(alias, "design", "Title", "Summary", "body", 0))
    assert run(HandoffStore().get(workspace, "design"))["content"] == "body"
    assert list(workspace.root.iterdir()) == []


def test_empty_list_does_not_create_internal_storage(workspace, isolated_user_data):
    assert run(HandoffStore().list(workspace)) == {"handoffs": []}
    assert not isolated_user_data.exists()


def test_store_uses_workspace_identity_without_accessing_workspace_files(workspace, monkeypatch):
    project_file = workspace.root / "notes.md"
    project_file.write_bytes(b"project data")
    def fail(*args, **kwargs):
        pytest.fail("Handoff storage must not resolve paths inside the workspace")
    monkeypatch.setattr(workspace, "resolve_path", fail)
    store = HandoffStore()
    assert run(store.list(workspace)) == {"handoffs": []}
    saved = run(store.save(workspace, "design", "Title", "Summary", "body", 0))
    assert run(store.list(workspace)) == {"handoffs": [saved]}
    assert run(store.get(workspace, "design")) == {**saved, "content": "body"}
    assert list(workspace.root.iterdir()) == [project_file]
    assert project_file.read_bytes() == b"project data"


def test_workspaces_are_isolated(tmp_path):
    roots = [tmp_path / name for name in ("project-A", "project-B")]
    for root in roots:
        root.mkdir()
    workspaces = [WorkspaceManager(root) for root in roots]
    store = HandoffStore()
    for workspace, body in zip(workspaces, ("A", "B")):
        assert run(store.save(workspace, "design", "Title", "Summary", body, 0))["revision"] == 1
    assert handoff_directory(workspaces[0]) != handoff_directory(workspaces[1])
    assert [run(store.get(workspace, "design"))["content"] for workspace in workspaces] == ["A", "B"]


def test_readable_key_collision_has_stable_hash_fallback(tmp_path, isolated_user_data):
    first, second = tmp_path / "a-b", tmp_path / "a/b"
    for root in (first, second):
        root.mkdir(parents=True)
    first, second = first.resolve(), second.resolve()
    assert workspace_key(str(first)) == workspace_key(str(second))
    primary = storage_directory(first, isolated_user_data, create=True)
    fallback = storage_directory(second, isolated_user_data, create=True)
    digest = hashlib.sha256(os.path.normcase(str(second)).encode()).hexdigest()[:8]
    assert fallback.name == f"{primary.name[:MAX_WORKSPACE_KEY_BYTES - 9]}-{digest}"
    assert storage_directory(second, isolated_user_data) == fallback
    assert storage_directory(first, isolated_user_data) == primary
    assert json.loads((fallback / "workspace.json").read_text())["canonical_path"] == str(second)


@pytest.mark.parametrize("create", [False, True])
def test_deleted_primary_still_resolves_existing_fallback(tmp_path, isolated_user_data, create):
    first, second = tmp_path / "a-b", tmp_path / "a/b"
    for root in (first, second):
        root.mkdir(parents=True)
    first, second = first.resolve(), second.resolve()
    assert workspace_key(str(first)) == workspace_key(str(second))
    primary = storage_directory(first, isolated_user_data, create=True)
    fallback = storage_directory(second, isolated_user_data, create=True)
    assert fallback != primary
    workspace = WorkspaceManager(second)
    store = HandoffStore()
    saved = run(store.save(workspace, "design", "Title", "Summary", "B handoff", 0))

    # Only delete A's temporary ownership metadata and now-empty directory.
    (primary / "workspace.json").unlink()
    primary.rmdir()
    assert storage_directory(second, isolated_user_data, create=create) == fallback
    assert not primary.exists()
    assert run(HandoffStore().list(workspace)) == {"handoffs": [saved]}
    assert run(HandoffStore().get(workspace, "design")) == {**saved, "content": "B handoff"}
    assert not primary.exists()


@pytest.mark.parametrize("metadata", [None, b"broken", b"[]", b"{}", b'{"canonical_path": 1}', b'{"canonical_path": "relative"}'])
def test_unclaimed_or_invalid_owner_is_never_overwritten(workspace, isolated_user_data, metadata):
    directory = isolated_user_data / "handoffs" / workspace_key(str(workspace.root))
    directory.mkdir(parents=True)
    if metadata is not None:
        (directory / "workspace.json").write_bytes(metadata)
    before = {entry.name: entry.read_bytes() for entry in directory.iterdir()}
    with pytest.raises(InvalidHandoffError, match="workspace.json"):
        storage_directory(workspace.root, isolated_user_data, create=True)
    assert {entry.name: entry.read_bytes() for entry in directory.iterdir()} == before


def test_exclusive_publish_failure_leaves_no_partial_destination(tmp_path, monkeypatch):
    destination = tmp_path / "workspace.json"
    def fail(*args):
        raise PermissionError("injected publish failure")
    monkeypatch.setattr(paths.os, "link", fail)
    with pytest.raises(OSError, match="publish failure"):
        paths.publish_bytes(destination, b"complete")
    assert list(tmp_path.iterdir()) == []


def test_concurrent_resolution_waits_for_ownership_publication(workspace, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    publish = paths.publish_bytes
    def delayed_publish(path, data):
        entered.set()
        assert release.wait(5), "ownership publisher was not released"
        publish(path, data)
    monkeypatch.setattr(paths, "publish_bytes", delayed_publish)

    async def scenario():
        store = HandoffStore()
        first = asyncio.create_task(store.save(workspace, "alpha", "A", "Summary A", "A", 0))
        others = []
        try:
            async with asyncio.timeout(3):
                while not entered.is_set():
                    await asyncio.sleep(0.01)
            others = [
                asyncio.create_task(store.save(workspace, "beta", "B", "Summary B", "B", 0)),
                asyncio.create_task(store.list(workspace)),
            ]
            await asyncio.sleep(0.03)
            assert not first.done() and all(not task.done() for task in others)
            release.set()
            results = await asyncio.gather(first, *others)
            assert results[0]["revision"] == results[1]["revision"] == 1
            assert await store.list(workspace) == {"handoffs": results[:2]}
            assert (await store.get(workspace, "alpha"))["content"] == "A"
            assert (await store.get(workspace, "beta"))["content"] == "B"
        finally:
            release.set()
            await asyncio.gather(first, *others, return_exceptions=True)
    run(scenario())


@pytest.mark.parametrize("kind", ["symlink", "junction"])
@pytest.mark.parametrize("location", ["base", "directory", "metadata", "file"])
def test_internal_storage_rejects_links(workspace, tmp_path, isolated_user_data, kind, location):
    if kind == "junction" and location in ("metadata", "file"):
        pytest.skip("Junctions are directory links")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_bytes(b"keep")
    if location == "base":
        isolated_user_data.mkdir()
        create_link(isolated_user_data / "handoffs", outside, kind)
    elif location == "directory":
        base = isolated_user_data / "handoffs"
        base.mkdir(parents=True)
        create_link(base / workspace_key(str(workspace.root)), outside, kind)
    else:
        directory = handoff_directory(workspace)
        if location == "metadata":
            (directory / "workspace.json").unlink()
        create_link(directory / ("workspace.json" if location == "metadata" else "design.md"), secret, kind)
    store = HandoffStore()
    for call in (lambda: store.list(workspace), lambda: store.get(workspace, "design"),
                 lambda: store.save(workspace, "design", "Title", "Summary", "new", 0)):
        with pytest.raises(InvalidHandoffError, match="symlinks"):
            run(call())
    assert secret.read_bytes() == b"keep"
