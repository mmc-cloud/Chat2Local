"""Workspace identities, ownership, safe legacy migration and retry behavior."""

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

from chat2local.handoff import paths, store as store_module
from chat2local.handoff.models import InvalidHandoffError, RevisionConflictError
from chat2local.handoff.paths import MAX_WORKSPACE_KEY_BYTES, storage_directory, workspace_key
from chat2local.handoff.store import HandoffStore
from chat2local.runtime import config
from chat2local.runtime.config import user_data_directory
from chat2local.runtime.workspace import WorkspaceManager
from conftest import handoff_directory, run
from test_apply_patch import create_link


@pytest.mark.parametrize("canonical,key", [
    (r"D:\Projects\Chat2Local", "D--杂物-vibe项目-Chat2Local-chat2local"),
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
    assert not (workspace.root / "HANDOFFS").exists()


def test_empty_list_does_not_create_internal_storage(workspace, isolated_user_data):
    assert run(HandoffStore().list(workspace)) == {"handoffs": []}
    assert not isolated_user_data.exists()


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


def legacy_files(workspace):
    directory = workspace.root / "HANDOFFS"
    directory.mkdir()
    data = {
        "alpha.md": "---\nrevision: 7\nupdated_at: 2026-10-03T02:30:00.123Z\ntitle: 标题 🚀\nsummary: 摘要\n---\n\n正文\r\n".encode(),
        "beta.md": b"---\nrevision: 3\nupdated_at: 2026-10-03T02:30:00+00:00\n---\n\nlegacy body",
        "notes.txt": b"keep ignored files too",
    }
    for name, content in data.items():
        (directory / name).write_bytes(content)
    return directory, data


@pytest.mark.parametrize("access", ["list", "get", "save"])
def test_first_access_migrates_all_bytes_and_preserves_records(workspace, access):
    legacy, data = legacy_files(workspace)
    store = HandoffStore()
    if access == "list":
        assert [item["workstream"] for item in run(store.list(workspace))["handoffs"]] == ["alpha", "beta"]
    elif access == "get":
        assert run(store.get(workspace, "alpha"))["revision"] == 7
    else:
        with pytest.raises(RevisionConflictError, match="current revision 7"):
            run(store.save(workspace, "alpha", "Title", "Summary", "must not write", 0))
    directory = handoff_directory(workspace)
    assert {name: (directory / name).read_bytes() for name in data} == data
    assert not legacy.exists()
    assert run(store.get(workspace, "alpha")) == dict(
        workstream="alpha", revision=7, updated_at="2026-10-03T02:30:00.123Z",
        title="标题 🚀", summary="摘要", content="正文\r\n",
    )
    assert run(store.get(workspace, "beta")) == dict(
        workstream="beta", revision=3, updated_at="2026-10-03T02:30:00+00:00",
        title=None, summary=None, content="legacy body",
    )
    updated = run(store.save(workspace, "alpha", "New", "Updated", "new body", 7))
    assert updated["revision"] == 8
    with pytest.raises(RevisionConflictError):
        run(store.save(workspace, "alpha", "New", "Updated", "stale", 7))


def test_existing_identical_and_disjoint_data_can_migrate(workspace):
    store = HandoffStore()
    run(store.save(workspace, "existing", "Title", "Summary", "existing", 0))
    directory = handoff_directory(workspace)
    legacy, data = legacy_files(workspace)
    (directory / "alpha.md").write_bytes(data["alpha.md"])
    existing = (directory / "existing.md").read_bytes()
    assert len(run(store.list(workspace))["handoffs"]) == 3
    assert not legacy.exists()
    assert (directory / "existing.md").read_bytes() == existing
    assert all((directory / name).read_bytes() == value for name, value in data.items())


def test_conflicting_new_data_preserves_all_old_and_new_files(workspace):
    directory = handoff_directory(workspace)
    legacy, data = legacy_files(workspace)
    (directory / "beta.md").write_bytes(b"different")
    for call in (lambda s: s.list(workspace), lambda s: s.get(workspace, "alpha"),
                 lambda s: s.save(workspace, "alpha", "Title", "Summary", "new", 7)):
        with pytest.raises(InvalidHandoffError, match="migration conflict: beta.md"):
            run(call(HandoffStore()))
        assert {entry.name: entry.read_bytes() for entry in legacy.iterdir()} == data
        assert (directory / "beta.md").read_bytes() == b"different"
        assert not (directory / "alpha.md").exists()


def test_failed_partial_copy_keeps_every_old_file_and_retries(workspace, monkeypatch):
    legacy, data = legacy_files(workspace)
    publish = store_module.publish_bytes
    def fail_second(path, content):
        if path.name == "beta.md":
            raise PermissionError("injected migration failure")
        publish(path, content)
    with monkeypatch.context() as patch:
        patch.setattr(store_module, "publish_bytes", fail_second)
        with pytest.raises(OSError, match="injected migration failure"):
            run(HandoffStore().list(workspace))
    assert {entry.name: entry.read_bytes() for entry in legacy.iterdir()} == data
    directory = handoff_directory(workspace)
    assert (directory / "alpha.md").read_bytes() == data["alpha.md"]
    assert not (directory / "beta.md").exists()
    assert not list(directory.glob(".handoff-*"))
    assert len(run(HandoffStore().list(workspace))["handoffs"]) == 2
    assert not legacy.exists()


def test_exclusive_publish_failure_leaves_no_partial_destination(tmp_path, monkeypatch):
    destination = tmp_path / "record.md"
    def fail(*args):
        raise PermissionError("injected publish failure")
    monkeypatch.setattr(paths.os, "link", fail)
    with pytest.raises(OSError, match="publish failure"):
        paths.publish_bytes(destination, b"complete")
    assert list(tmp_path.iterdir()) == []


def test_concurrent_first_access_and_saves_keep_revision_guarantee(workspace):
    legacy, _ = legacy_files(workspace)
    async def scenario():
        store = HandoffStore()
        results = await asyncio.gather(
            store.save(workspace, "alpha", "A", "Summary A", "A", 7),
            store.save(workspace, "alpha", "B", "Summary B", "B", 7),
            store.get(workspace, "beta"), store.list(workspace), return_exceptions=True,
        )
        assert sum(isinstance(result, dict) for result in results[:2]) == 1
        assert sum(isinstance(result, RevisionConflictError) for result in results[:2]) == 1
        assert results[2]["revision"] == 3
        assert len(results[3]["handoffs"]) == 2
        assert (await store.get(workspace, "alpha"))["revision"] == 8
    run(scenario())
    assert not legacy.exists()


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
