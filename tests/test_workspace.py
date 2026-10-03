from pathlib import Path

import pytest
import os
import subprocess

from chat2local.runtime.workspace import WorkspaceError, WorkspaceManager


def test_resolve_path_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    file = workspace / "test.txt"
    file.write_text("hello", encoding="utf-8")

    manager = WorkspaceManager(workspace)

    result = manager.resolve_path("test.txt")

    assert result == file.resolve()


def test_reject_parent_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    manager = WorkspaceManager(workspace)

    with pytest.raises(WorkspaceError):
        manager.resolve_path("../outside.txt")


def test_reject_absolute_path_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    outside = tmp_path / "outside.txt"

    manager = WorkspaceManager(workspace)

    with pytest.raises(WorkspaceError):
        manager.resolve_path(outside)

def test_reject_symlink_to_file_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")

    link = workspace / "link.txt"

    try:
        link.symlink_to(outside_file)
    except OSError:
        pytest.skip("Symlinks are not available on this system")

    manager = WorkspaceManager(workspace)

    with pytest.raises(WorkspaceError):
        manager.resolve_path("link.txt")


def test_reject_path_through_symlink_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    link_dir = workspace / "linked-dir"

    try:
        link_dir.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are not available on this system")

    manager = WorkspaceManager(workspace)

    with pytest.raises(WorkspaceError):
        manager.resolve_path("linked-dir/new-file.txt")


@pytest.mark.parametrize("allowed_roots", [None, []])
def test_default_allowed_roots_are_only_the_workspace(tmp_path: Path, allowed_roots) -> None:
    manager = WorkspaceManager(tmp_path, allowed_roots=allowed_roots)

    assert manager.allowed_roots == (tmp_path.resolve(),)

    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    with pytest.raises(WorkspaceError, match="outside allowed roots"):
        manager.select_workspace(outside)


@pytest.mark.parametrize("subdirectory", [".", "projectA", "nested/projectA"])
def test_workspace_can_be_allowed_root_or_descendant(tmp_path: Path, subdirectory: str) -> None:
    workspace = tmp_path / subdirectory
    workspace.mkdir(parents=True, exist_ok=True)
    manager = WorkspaceManager(workspace, allowed_roots=[tmp_path])

    assert manager.root == workspace.resolve()
    assert manager.allowed_roots == (tmp_path.resolve(),)


def test_reject_workspace_outside_allowed_roots(tmp_path: Path) -> None:
    allowed = tmp_path / "project"
    allowed.mkdir()
    outside = tmp_path / "project-other"
    outside.mkdir()

    with pytest.raises(WorkspaceError, match="Workspace is outside allowed roots"):
        WorkspaceManager(outside, allowed_roots=[allowed])


@pytest.mark.parametrize("index", [0, 1])
def test_workspace_can_use_any_allowed_root(tmp_path: Path, index: int) -> None:
    roots = [tmp_path / "rootA", tmp_path / "rootB"]
    for root in roots:
        root.mkdir()
    workspace = roots[index] / "project"
    workspace.mkdir()

    manager = WorkspaceManager(workspace, allowed_roots=roots)

    assert manager.allowed_roots == tuple(root.resolve() for root in roots)


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_allowed_root_is_rejected_even_with_another_valid_root(
    tmp_path: Path, kind: str
) -> None:
    invalid = tmp_path / "invalid"
    if kind == "file":
        invalid.write_text("file", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="Allowed root"):
        WorkspaceManager(tmp_path, allowed_roots=[tmp_path, invalid])


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_workspace_is_rejected(tmp_path: Path, kind: str) -> None:
    invalid = tmp_path / "invalid"
    if kind == "file":
        invalid.write_text("file", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="Workspace"):
        WorkspaceManager(invalid, allowed_roots=[tmp_path])


def test_select_workspace_preserves_default_and_allowed_roots(tmp_path: Path) -> None:
    project_a = tmp_path / "projectA"
    project_b = tmp_path / "projectB"
    project_a.mkdir()
    project_b.mkdir()
    manager = WorkspaceManager(project_a, allowed_roots=[tmp_path])

    selected = manager.select_workspace(project_b)

    assert selected is not manager
    assert selected.root == project_b.resolve()
    assert selected.allowed_roots == manager.allowed_roots
    assert manager.root == project_a.resolve()
    with pytest.raises(WorkspaceError, match="outside workspace"):
        selected.resolve_path("../projectA")


def test_select_workspace_rejects_outside_allowed_roots(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()

    with pytest.raises(WorkspaceError, match="outside allowed roots"):
        WorkspaceManager(allowed).select_workspace(outside)


@pytest.mark.parametrize("relative", [".", "projectB", "../projectB", ""])
def test_select_workspace_requires_absolute_path(tmp_path: Path, relative: str) -> None:
    with pytest.raises(WorkspaceError, match="absolute path"):
        WorkspaceManager(tmp_path).select_workspace(relative)


@pytest.mark.parametrize("link_kind", ["symlink", "junction"])
@pytest.mark.parametrize("boundary", ["workspace", "allowed_root"])
def test_directory_links_cannot_escape_security_boundaries(
    tmp_path: Path, link_kind: str, boundary: str
) -> None:
    allowed = tmp_path / "allowed"
    project = allowed / "project"
    project.mkdir(parents=True)
    outside = (allowed if boundary == "workspace" else tmp_path) / "outside"
    outside.mkdir()
    link = project / "linked"
    if link_kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction test")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip(f"Could not create junction: {result.stderr}")
    else:
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("Symlinks are not available on this system")

    manager = WorkspaceManager(project, allowed_roots=[allowed])
    with pytest.raises(WorkspaceError, match="outside workspace"):
        manager.resolve_path("linked/secret.txt")
    if boundary == "allowed_root":
        with pytest.raises(WorkspaceError, match="outside allowed roots"):
            WorkspaceManager(link, allowed_roots=[allowed])
        with pytest.raises(WorkspaceError, match="outside allowed roots"):
            manager.select_workspace(link)
    else:
        assert manager.select_workspace(link).root == outside.resolve()


def test_allow_symlink_that_stays_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    real_dir = workspace / "real"
    real_dir.mkdir()

    file = real_dir / "test.txt"
    file.write_text("hello", encoding="utf-8")

    link_dir = workspace / "linked-dir"

    try:
        link_dir.symlink_to(real_dir, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are not available on this system")

    manager = WorkspaceManager(workspace)

    result = manager.resolve_path("linked-dir/test.txt")

    assert result == file.resolve()


def test_reject_path_through_windows_junction(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction test")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    junction = workspace / "linked-dir"

    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside_dir)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        pytest.skip(f"Could not create junction: {result.stderr}")

    manager = WorkspaceManager(workspace)

    with pytest.raises(WorkspaceError):
        manager.resolve_path("linked-dir/new-file.txt")
