"""Patch preparation, strict matching, races and actual filesystem commits."""

import codecs
import errno
import os
from pathlib import Path
import subprocess

import pytest

from chat2local.tools import apply_patch as module
from chat2local.tools.apply_patch import PatchError, apply_patch, commit_patch, prepare_patch
from chat2local.runtime.workspace import WorkspaceManager
from conftest import make_file, run


def patch(body: str) -> str:
    return f"*** Begin Patch\n{body}\n*** End Patch\n"


def update(path="file.txt", old="old", new="new"):
    return f"*** Update File: {path}\n@@\n-{old}\n+{new}"


def test_add_file_utf8_no_bom_lf(workspace):
    result = run(apply_patch(workspace, patch("*** Add File: pkg/你好.txt\n+你好\n+world").replace("\n", "\r\n")))
    assert (workspace.root / "pkg/你好.txt").read_bytes() == "你好\nworld\n".encode()
    assert result == {"success": True, "partial": False, "operations": [
        {"action": "add", "path": "pkg/你好.txt"}], "created_directories": ["pkg"],
        "temporary_files": [], "failed_operation": None, "error": None}
    assert list(workspace.root.rglob(".chat2local-patch-*")) == []


def test_add_empty_file(workspace):
    assert run(apply_patch(workspace, patch("*** Add File: empty")))["success"]
    assert (workspace.root / "empty").read_bytes() == b""


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("encoding,bom", [
    ("utf-8", b""), ("utf-8", codecs.BOM_UTF8),
    ("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE),
])
@pytest.mark.parametrize("terminated", [True, False])
def test_update_preserves_encoding_newlines_and_final_newline(workspace, newline, encoding, bom, terminated):
    path = workspace.root / "file.txt"
    original = newline.join(["before", "old", "after"]) + (newline if terminated else "")
    path.write_bytes(bom + original.encode(encoding))
    result = run(apply_patch(workspace, patch("*** Update File: file.txt\n@@\n before\n-old\n+你好\n after")))
    assert result["success"]
    assert path.read_bytes() == bom + original.replace("old", "你好").encode(encoding)


def test_update_multiple_hunks_exact_anchor_and_eof(workspace):
    path = make_file(workspace, "file.txt", "first\nold\nbetween\nanchor\nold\n")
    result = run(apply_patch(workspace, patch(
        "*** Update File: file.txt\n@@\n first\n-old\n+one\n@@ anchor\n-old\n+two\n*** End of File")))
    assert result["success"] and path.read_bytes() == b"first\none\nbetween\nanchor\ntwo\n"


@pytest.mark.parametrize("original,body,expected", [
    ("", "@@\n+one", b"one"),
    ("old\n", "@@\n-old", b""),
    ("old\n", "@@\n old\n+last", b"old\nlast\n"),
    ("old\n", "@@\n+last\n*** End of File", b"old\nlast\n"),
])
def test_empty_file_and_insert_delete_hunks(workspace, original, body, expected):
    path = make_file(workspace, "file.txt", original)
    assert run(apply_patch(workspace, patch(f"*** Update File: file.txt\n{body}")))["success"]
    assert path.read_bytes() == expected


def test_delete_and_move_binary_files_use_real_filesystem_move(workspace, monkeypatch):
    source = workspace.root / "file.bin"
    source.write_bytes(b"\x00\xffbinary")
    identity = source.stat().st_ino
    original = module._rename_no_overwrite
    calls = []

    def rename(source, destination):
        calls.append((source, destination))
        original(source, destination)

    monkeypatch.setattr(module, "_rename_no_overwrite", rename)
    moved = run(apply_patch(workspace, patch("*** Update File: file.bin\n*** Move to: nested/new.bin")))
    assert moved["success"] and moved["operations"] == [
        {"action": "move", "path": "file.bin", "destination": "nested/new.bin"}]
    assert not source.exists()
    destination = workspace.root / "nested/new.bin"
    assert destination.read_bytes() == b"\x00\xffbinary" and destination.stat().st_ino == identity
    assert calls == [(source, destination)]
    assert run(apply_patch(workspace, patch("*** Delete File: nested/new.bin")))["success"]
    assert not destination.exists()


def test_directory_move_preserves_tree_and_identity(workspace):
    source = workspace.root / "old"
    (source / "empty").mkdir(parents=True)
    make_file(workspace, "old/sub/file.txt", "hello")
    identity = source.stat().st_ino
    result = run(apply_patch(workspace, patch("*** Move Directory: old\n*** Move to: nested/new")))
    assert result["success"] and result["operations"][0]["action"] == "move_directory"
    assert not source.exists()
    target = workspace.root / "nested/new"
    assert target.stat().st_ino == identity
    assert (target / "sub/file.txt").read_text() == "hello"
    assert (target / "empty").is_dir()


@pytest.mark.parametrize("move_first", [True, False])
def test_update_and_move_can_place_move_header_before_or_after_hunk(workspace, move_first):
    make_file(workspace, "file.txt", "old\n")
    move = "*** Move to: moved.txt"
    hunk = "@@\n-old\n+new"
    body = "*** Update File: file.txt\n" + (f"{move}\n{hunk}" if move_first else f"{hunk}\n{move}")
    result = run(apply_patch(workspace, patch(body)))
    assert result["success"]
    assert [operation["action"] for operation in result["operations"]] == ["update", "move"]
    assert (workspace.root / "moved.txt").read_bytes() == b"new\n"
    assert not (workspace.root / "file.txt").exists()


def test_multi_operation_prepare_produces_complete_plan_without_writes(workspace, monkeypatch):
    first = make_file(workspace, "file.txt", "old\n")
    removed = make_file(workspace, "delete.txt", "delete\n")
    moved = make_file(workspace, "source.txt", "move\n")
    def forbidden(*args, **kwargs):
        raise AssertionError("Prepare must not write")
    with monkeypatch.context() as guard:
        guard.setattr(module.tempfile, "NamedTemporaryFile", forbidden)
        guard.setattr(Path, "mkdir", forbidden)
        guard.setattr(Path, "unlink", forbidden)
        guard.setattr(module.os, "replace", forbidden)
        guard.setattr(module, "_rename_no_overwrite", forbidden)
        plan = prepare_patch(workspace, patch(
            "*** Add File: nested/add.txt\n+added\n" + update() +
            "\n*** Delete File: delete.txt\n*** Update File: source.txt\n*** Move to: target.txt"))
    assert [change.action for change in plan.changes] == ["add", "update", "delete", "move"]
    assert plan.changes[0].content == b"added\n" and plan.changes[1].content == b"new\n"
    assert first.read_bytes() == b"old\n" and removed.exists() and moved.exists()
    assert not (workspace.root / "nested").exists()
    result = commit_patch(plan)
    assert result["success"] and len(result["operations"]) == 4
    assert first.read_bytes() == b"new\n" and not removed.exists() and not moved.exists()
    assert (workspace.root / "target.txt").read_bytes() == b"move\n"


@pytest.mark.parametrize("body", [
    "*** Copy File: file.txt\n*** Copy to: other.txt",
    "*** Copy Directory: dir\n*** Copy to: other",
    "*** Delete Directory: dir", "*** Move to: other",
    "*** Add File: x\nunprefixed", "*** Add File: x\n*** Move to: y",
    "*** Update File: file.txt", "*** Update File: file.txt\n@@",
    "*** Update File: file.txt\n@@\n old", "*** Move Directory: dir",
    "*** Update File: file.txt\n*** Move to: a\n*** Move to: b",
    "*** Add File: ", "*** Add File: x\r+content", "*** Add File: x\n+\x00",
    "*** Update File: file.txt\n@@\n-old\n+new\n*** End of File\n@@\n-old\n+new",
])
def test_malformed_or_unsupported_patch_cannot_write(workspace, body):
    before = make_file(workspace, "file.txt", "old\n")
    with pytest.raises(PatchError):
        run(apply_patch(workspace, patch("*** Add File: first.txt\n+first\n" + body)))
    assert before.read_bytes() == b"old\n" and not (workspace.root / "first.txt").exists()


@pytest.mark.parametrize("raw", ["", "*** Begin Patch\n*** End Patch", "bad", "*** Begin Patch\n*** Add File: x\n+x"])
def test_invalid_envelope(raw, workspace):
    with pytest.raises(PatchError):
        prepare_patch(workspace, raw)


@pytest.mark.parametrize("contents,body,message", [
    ("old\n", update(old=" Old"), "exactly"),
    ("old\nold\n", update(), "ambiguous"),
    ("old\n", "*** Update File: file.txt\n@@\n+new", "ambiguous"),
    ("anchor\nold\nanchor\nold\n", "*** Update File: file.txt\n@@ anchor\n-old\n+new", "ambiguous"),
    ("old\n", "*** Update File: file.txt\n@@ missing\n-old\n+new", "anchor"),
])
def test_context_never_guesses(workspace, contents, body, message):
    source = make_file(workspace, "file.txt", contents)
    with pytest.raises(PatchError, match=message):
        prepare_patch(workspace, patch(body))
    assert source.read_bytes() == contents.encode()


@pytest.mark.parametrize("bad", [
    "*** Add File: file.txt\n+overwrite",
    "*** Update File: file.txt\n*** Move to: occupied.txt",
    "*** Delete File: missing.txt", update("missing.txt"),
    "*** Delete File: dir", "*** Update File: dir\n*** Move to: other",
    "*** Move Directory: file.txt\n*** Move to: other",
    "*** Move Directory: missing\n*** Move to: other",
    "*** Move Directory: dir\n*** Move to: dir/child",
    "*** Move Directory: dir\n*** Move to: occupied.txt",
    "*** Add File: file.txt/child\n+new",
    "*** Add File: same\n+one\n*** Add File: same\n+two",
    "*** Add File: parent\n+one\n*** Add File: parent/child\n+two",
    update() + "\n*** Delete File: file.txt",
])
def test_late_prepare_failure_keeps_every_operation_unwritten(workspace, bad):
    source = make_file(workspace, "file.txt", "old\n")
    make_file(workspace, "occupied.txt", "occupied\n")
    (workspace.root / "dir").mkdir()
    before = sorted(str(path.relative_to(workspace.root)) for path in workspace.root.rglob("*"))
    with pytest.raises(PatchError):
        run(apply_patch(workspace, patch("*** Add File: new/first.txt\n+first\n" + bad)))
    assert source.read_bytes() == b"old\n"
    assert sorted(str(path.relative_to(workspace.root)) for path in workspace.root.rglob("*")) == before


@pytest.mark.parametrize("action", ["update", "delete", "move"])
def test_source_hash_conflict_does_not_overwrite_external_edits(workspace, action):
    source = make_file(workspace, "file.txt", "old\n")
    body = {"update": update(), "delete": "*** Delete File: file.txt",
            "move": "*** Update File: file.txt\n*** Move to: moved.txt"}[action]
    plan = prepare_patch(workspace, patch(body))
    source.write_bytes(b"external\n")
    result = commit_patch(plan)
    assert not result["success"] and not result["partial"] and result["operations"] == []
    assert "SHA-256" in result["error"] and source.read_bytes() == b"external\n"
    assert not (workspace.root / "moved.txt").exists()


@pytest.mark.parametrize("mutation", ["edit", "add", "remove", "directory"])
def test_directory_move_checks_all_source_files_and_structure(workspace, mutation):
    source = make_file(workspace, "dir/file.txt", "old\n")
    plan = prepare_patch(workspace, patch("*** Move Directory: dir\n*** Move to: moved"))
    if mutation == "edit":
        source.write_bytes(b"external")
    elif mutation == "add":
        make_file(workspace, "dir/new.txt", "external")
    elif mutation == "remove":
        source.unlink()
    else:
        (source.parent / "new_dir").mkdir()
    result = commit_patch(plan)
    assert not result["success"] and "conflict" in result["error"]
    assert source.parent.exists() and not (workspace.root / "moved").exists()


@pytest.mark.parametrize("action", ["add", "move", "move_directory"])
def test_destination_appearing_after_prepare_is_not_overwritten(workspace, action):
    make_file(workspace, "file.txt", "old\n")
    (workspace.root / "dir").mkdir()
    body = {"add": "*** Add File: target\n+new", "move": "*** Update File: file.txt\n*** Move to: target",
            "move_directory": "*** Move Directory: dir\n*** Move to: target"}[action]
    plan = prepare_patch(workspace, patch(body))
    target = workspace.root / "target"
    target.write_bytes(b"external")
    result = commit_patch(plan)
    assert not result["success"] and "exists" in result["error"]
    assert target.read_bytes() == b"external" and (workspace.root / "file.txt").read_bytes() == b"old\n"


@pytest.mark.parametrize("action", ["add", "move", "move_directory"])
def test_no_overwrite_finalization_survives_race_after_last_check(workspace, action, monkeypatch):
    make_file(workspace, "file.txt", "old\n")
    (workspace.root / "dir").mkdir()
    target = workspace.root / "target"
    body = {"add": "*** Add File: target\n+new", "move": "*** Update File: file.txt\n*** Move to: target",
            "move_directory": "*** Move Directory: dir\n*** Move to: target"}[action]
    name = "link" if action == "add" and os.name != "nt" else "_rename_no_overwrite"
    owner = module.os if name == "link" else module
    original = getattr(owner, name)
    def race(source, destination):
        target.write_bytes(b"external")
        return original(source, destination)
    monkeypatch.setattr(owner, name, race)
    result = run(apply_patch(workspace, patch(body)))
    assert not result["success"] and target.read_bytes() == b"external"
    assert (workspace.root / "file.txt").read_bytes() == b"old\n"
    assert list(workspace.root.glob(".chat2local-patch-*")) == []


def test_update_uses_closed_same_directory_temp_then_replace(workspace, monkeypatch):
    source = make_file(workspace, "pkg/file.txt", "old\n")
    original = module.os.replace
    calls = []
    def replace(temporary, destination):
        assert temporary.parent == source.parent and temporary != source
        assert temporary.read_bytes() == b"new\n" and source.read_bytes() == b"old\n"
        calls.append((temporary, destination))
        original(temporary, destination)
    monkeypatch.setattr(module.os, "replace", replace)
    assert run(apply_patch(workspace, patch(update("pkg/file.txt"))))["success"]
    assert len(calls) == 1 and source.read_bytes() == b"new\n" and not calls[0][0].exists()


def test_update_rechecks_hash_after_temp_is_written(workspace, monkeypatch):
    source = make_file(workspace, "file.txt", "old\n")
    original = module.os.chmod
    def race(path, mode):
        source.write_bytes(b"external\n")
        original(path, mode)
    monkeypatch.setattr(module.os, "chmod", race)
    result = run(apply_patch(workspace, patch(update())))
    assert not result["success"] and "SHA-256" in result["error"]
    assert source.read_bytes() == b"external\n" and list(workspace.root.glob(".chat2local-patch-*")) == []


@pytest.mark.parametrize("failure", ["create", "write", "replace"])
def test_temp_failure_leaves_original_intact(workspace, monkeypatch, failure):
    source = make_file(workspace, "file.txt", "old\n")
    def fail(*args, **kwargs):
        raise OSError(errno.ENOSPC, "Disk full", "HOST-ABSOLUTE-PATH")
    if failure == "create":
        monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", fail)
    elif failure == "replace":
        monkeypatch.setattr(module.os, "replace", fail)
    else:
        original = module.tempfile.NamedTemporaryFile
        class BrokenWrite:
            def __init__(self, *args, **kwargs):
                self.handle = original(*args, **kwargs)
                self.name = self.handle.name
            def __enter__(self):
                return self
            def write(self, data):
                self.handle.write(data[:1])
                fail()
            def __exit__(self, *args):
                self.handle.close()
        monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", BrokenWrite)
    result = run(apply_patch(workspace, patch(update())))
    assert not result["success"] and not result["partial"] and result["error"] == "Disk full"
    assert source.read_bytes() == b"old\n" and list(workspace.root.glob(".chat2local-patch-*")) == []


def test_partial_commit_reports_completed_update_before_failed_move(workspace, monkeypatch):
    source = make_file(workspace, "file.txt", "old\n")
    def fail(*args):
        raise OSError(errno.EXDEV, "Cross-device move")
    monkeypatch.setattr(module, "_rename_no_overwrite", fail)
    result = run(apply_patch(workspace, patch(update() + "\n*** Move to: other")))
    assert result["success"] is False and result["partial"] is True
    assert result["operations"] == [{"action": "update", "path": "file.txt"}]
    assert result["failed_operation"] == {"action": "move", "path": "file.txt", "destination": "other"}
    assert source.read_bytes() == b"new\n" and not (workspace.root / "other").exists()


def test_partial_commit_reports_prior_operations_and_created_directories(workspace, monkeypatch):
    source = make_file(workspace, "file.txt", "old\n")
    def fail(*args):
        raise PermissionError(errno.EACCES, "Denied")
    monkeypatch.setattr(module.os, "replace", fail)
    result = run(apply_patch(workspace, patch("*** Add File: nested/new\n+new\n" + update())))
    assert not result["success"] and result["partial"]
    assert result["operations"] == [{"action": "add", "path": "nested/new"}]
    assert result["created_directories"] == ["nested"]
    assert source.read_bytes() == b"old\n" and (workspace.root / "nested/new").read_bytes() == b"new\n"


def test_created_parent_is_reported_even_if_first_file_write_fails(workspace, monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError(errno.EACCES, "Denied")
    monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", fail)
    result = run(apply_patch(workspace, patch("*** Add File: nested/new\n+new")))
    assert result["partial"] and not result["success"] and result["operations"] == []
    assert result["created_directories"] == ["nested"] and (workspace.root / "nested").is_dir()


@pytest.mark.parametrize("contents", [b"\xff\xfebad", b"old\x00", b"old\r\nnext\n"])
def test_unsupported_or_mixed_text_fails_prepare_without_writing(workspace, contents):
    source = workspace.root / "file.txt"
    source.write_bytes(contents)
    with pytest.raises(PatchError):
        prepare_patch(workspace, patch("*** Add File: first\n+new\n" + update()))
    assert source.read_bytes() == contents and not (workspace.root / "first").exists()


@pytest.mark.parametrize("kind", ["add", "source", "destination", "directory"])
@pytest.mark.parametrize("absolute", [True, False])
def test_workspace_escape_cannot_write(tmp_path, kind, absolute):
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = WorkspaceManager(root, allowed_roots=[tmp_path])
    make_file(workspace, "file.txt", "old\n")
    (tmp_path / "outside").mkdir()
    target = str(tmp_path / "outside/file.txt") if absolute else "../outside/file.txt"
    body = {"add": f"*** Add File: {target}\n+new", "source": f"*** Delete File: {target}",
            "destination": f"*** Update File: file.txt\n*** Move to: {target}",
            "directory": f"*** Move Directory: ../outside\n*** Move to: dir"}[kind]
    with pytest.raises(PatchError, match="outside workspace"):
        prepare_patch(workspace, patch(body))
    assert (root / "file.txt").read_bytes() == b"old\n" and list((tmp_path / "outside").iterdir()) == []


def create_link(link, target, kind):
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction test")
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if result.returncode:
            pytest.skip(f"Cannot create junction: {result.stderr}")
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("Symlinks unavailable on this system")


@pytest.mark.parametrize("kind", ["symlink", "junction"])
@pytest.mark.parametrize("location", ["outside", "inside"])
def test_link_paths_and_directory_trees_are_conservatively_rejected(tmp_path, kind, location):
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = WorkspaceManager(root)
    target = (tmp_path if location == "outside" else root) / "target"
    target.mkdir()
    (target / "secret").write_bytes(b"keep")
    (root / "dir").mkdir()
    create_link(root / "dir/link", target, kind)
    for body in ("*** Add File: dir/link/new\n+new", "*** Delete File: dir/link/secret",
                 "*** Move Directory: dir\n*** Move to: other",
                 "*** Update File: dir/link/secret\n*** Move to: moved"):
        with pytest.raises(PatchError):
            prepare_patch(workspace, patch(body))
    assert (target / "secret").read_bytes() == b"keep" and not (target / "new").exists()


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_commit_rechecks_parent_link_replacement(tmp_path, kind):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "dir").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = WorkspaceManager(root)
    plan = prepare_patch(workspace, patch("*** Add File: dir/new\n+new"))
    (root / "dir").rmdir()
    create_link(root / "dir", outside, kind)
    result = commit_patch(plan)
    assert not result["success"] and not result["partial"] and list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="NTFS path rules")
@pytest.mark.parametrize("path", [
    "file.txt:stream", "NUL", "file.txt.", "dir./file.txt", "file?.txt", "file*.txt", 'file".txt', "dir\t/file.txt",
])
def test_reject_ntfs_streams_reserved_and_aliased_paths(workspace, path):
    make_file(workspace, "file.txt", "keep")
    with pytest.raises(PatchError):
        prepare_patch(workspace, patch(f"*** Add File: first\n+first\n*** Add File: {path}\n+new"))
    assert (workspace.root / "file.txt").read_bytes() == b"keep"
    assert not (workspace.root / "first").exists()


def test_absolute_in_workspace_paths_return_only_relative_paths(workspace):
    source = workspace.root / "file.txt"
    result = run(apply_patch(workspace, patch(f"*** Add File: {source}\n+new")))
    assert result["operations"] == [{"action": "add", "path": "file.txt"}]
    with pytest.raises(PatchError) as failure:
        prepare_patch(workspace, patch(f"*** Add File: {source}\n+new"))
    assert str(workspace.root) not in str(failure.value)


def test_parse_finishes_before_any_source_is_read(workspace, monkeypatch):
    make_file(workspace, "file.txt", "old\n")
    def forbidden(*args):
        raise AssertionError("Malformed patch must fail before reading sources")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    with pytest.raises(PatchError):
        prepare_patch(workspace, patch(update() + "\n*** Copy File: nope"))


def test_second_file_context_failure_does_not_commit_first_update(workspace):
    first = make_file(workspace, "file.txt", "old\n")
    second = make_file(workspace, "second.txt", "different\n")
    with pytest.raises(PatchError):
        prepare_patch(workspace, patch(update() + "\n" + update("second.txt")))
    assert first.read_bytes() == b"old\n" and second.read_bytes() == b"different\n"


@pytest.mark.parametrize("action", ["update", "delete", "move"])
def test_source_removed_after_prepare_is_reported_without_modification(workspace, action):
    source = make_file(workspace, "file.txt", "old\n")
    body = {"update": update(), "delete": "*** Delete File: file.txt",
            "move": "*** Update File: file.txt\n*** Move to: target"}[action]
    plan = prepare_patch(workspace, patch(body))
    source.unlink()
    result = commit_patch(plan)
    assert not result["success"] and not result["partial"] and not source.exists()


def test_cleanup_failure_truthfully_reports_already_finalized_operation(workspace, monkeypatch):
    source = workspace.root / "file.txt"
    # Exercise real hard-link finalization on NTFS too; unlike rename, it leaves
    # a temporary link that can genuinely fail to unlink after success.
    def finalize(temporary, destination):
        os.link(temporary, destination)
        return False
    monkeypatch.setattr(module, "_finalize_add", finalize)
    original = Path.unlink
    def fail_temp_cleanup(path, *args, **kwargs):
        if path.name.startswith(".chat2local-patch-"):
            raise PermissionError(errno.EACCES, "Temp cleanup denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_temp_cleanup)
    result = run(apply_patch(workspace, patch("*** Add File: file.txt\n+new")))
    assert not result["success"] and result["partial"] and source.read_bytes() == b"new\n"
    assert result["operations"] == [{"action": "add", "path": "file.txt"}]
    assert result["failed_operation"]["phase"] == "temporary_file_cleanup"
    assert len(result["temporary_files"]) == 1
    assert (workspace.root / result["temporary_files"][0]).read_bytes() == b"new\n"


@pytest.mark.parametrize("action", ["delete", "move_directory"])
def test_io_failure_preserves_prior_completed_operations(workspace, monkeypatch, action):
    source = make_file(workspace, "file.txt", "old\n")
    (workspace.root / "dir").mkdir()
    def fail(*args, **kwargs):
        raise PermissionError(errno.EACCES, "Denied")
    if action == "delete":
        original = Path.unlink
        def delete(path, *args, **kwargs):
            if path == source:
                fail()
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "unlink", delete)
        body = "*** Delete File: file.txt"
    else:
        original_rename = module._rename_no_overwrite
        def move(source, destination):
            if source.is_dir():
                fail()
            original_rename(source, destination)
        monkeypatch.setattr(module, "_rename_no_overwrite", move)
        body = "*** Move Directory: dir\n*** Move to: moved"
    result = run(apply_patch(workspace, patch("*** Add File: first\n+first\n" + body)))
    assert result["partial"] and result["operations"] == [{"action": "add", "path": "first"}]
    assert result["failed_operation"]["action"] == action
    assert source.read_bytes() == b"old\n" and (workspace.root / "dir").is_dir()


def test_update_preserves_permission_bits(workspace):
    source = make_file(workspace, "file.txt", "old\n")
    source.chmod(0o755)
    before = source.stat().st_mode & 0o777
    assert run(apply_patch(workspace, patch(update())))["success"]
    assert source.stat().st_mode & 0o777 == before


def test_file_changing_during_prepare_read_is_rejected(workspace, monkeypatch):
    source = make_file(workspace, "file.txt", "old\n")
    original = Path.read_bytes
    def read(path):
        data = original(path)
        if path == source:
            path.write_bytes(b"changed while reading\n")
        return data
    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(PatchError, match="changed while being read"):
        prepare_patch(workspace, patch("*** Add File: first\n+first\n" + update()))
    assert not (workspace.root / "first").exists()
