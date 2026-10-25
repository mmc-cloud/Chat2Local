"""Read-only local discovery, damaged entries, identities and transport parity."""

import json
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from chat2local.handoff.models import HandoffNotFoundError, InvalidHandoffError
from chat2local.handoff.paths import storage_directory, personal_storage_directory
from chat2local.handoff.store import HandoffStore
from chat2local.management.adapter import CoreAdapter
from chat2local.management.http import create_management_app
from chat2local.runtime.workspace import WorkspaceManager
from conftest import run
from test_apply_patch import create_link


@pytest.fixture
def local_handoffs(tmp_path, isolated_user_data):
    store = HandoffStore()
    for name in ("项目甲", "project-B"):
        root = tmp_path / name
        root.mkdir()
        run(store.save(WorkspaceManager(root), "design", name, f"Summary {name}", f"# {name}\n\n正文", 0))
        root.rmdir()  # Browsing must not require the original project to exist.
    run(store.save(None, "design", "Personal note", "Personal summary", "personal body", 0))
    return store


def test_all_scopes_distinct_ids_metadata_and_no_writes(local_handoffs, isolated_user_data):
    before = {p: p.read_bytes() for p in isolated_user_data.rglob("*") if p.is_file()}
    result = run(local_handoffs.list_local())
    assert result["issues"] == []
    assert len(result["handoffs"]) == 3
    assert len({entry["id"] for entry in result["handoffs"]}) == 3
    assert {entry["project"]["name"] for entry in result["handoffs"]} == {"项目甲", "project-B", "Personal"}
    for entry in result["handoffs"]:
        assert "content" not in entry and entry["workstream"] == "design"
        detail = run(HandoffStore().get_local(entry["id"]))
        assert {key: value for key, value in detail.items() if key != "content"} == entry
        assert detail["content"]
    assert {p: p.read_bytes() for p in isolated_user_data.rglob("*") if p.is_file()} == before


def test_empty_missing_and_empty_existing_directory(isolated_user_data):
    store = HandoffStore()
    assert run(store.list_local()) == {"handoffs": [], "issues": []}
    assert not isolated_user_data.exists()
    (isolated_user_data / "handoffs").mkdir(parents=True)
    assert run(store.list_local()) == {"handoffs": [], "issues": []}


def test_root_management_entries_are_ignored(local_handoffs, isolated_user_data):
    expected = run(local_handoffs.list_local())
    base = isolated_user_data / "handoffs"
    for name in (".git", ".cache"):
        (base / name).mkdir()
        (base / name / "workspace.json").write_bytes(b"not workspace metadata")
    for name in (".gitignore", "README.txt"):
        (base / name).write_bytes(b"not a workspace directory")

    result = run(local_handoffs.list_local())
    assert result == expected
    assert len(result["handoffs"]) == 3 and not result["issues"]
    for entry in result["handoffs"]:
        assert run(local_handoffs.get_local(entry["id"]))["content"]


@pytest.mark.parametrize("metadata", [None, b"broken"])
def test_root_filter_preserves_workspace_and_markdown_issues(local_handoffs, isolated_user_data, metadata):
    base = isolated_user_data / "handoffs"
    (base / ".git").mkdir()
    (base / ".gitignore").write_bytes(b"*.tmp\n")
    broken = base / "broken-workspace"
    broken.mkdir()
    if metadata is not None:
        (broken / "workspace.json").write_bytes(metadata)
    (base / "_personal" / "bad.md").write_bytes(b"broken Markdown")
    workspace = next(path for path in base.iterdir() if (path / "workspace.json").is_file() and path != broken)
    (workspace / "bad.md").write_bytes(b"\xff")

    result = run(local_handoffs.list_local())
    assert len(result["handoffs"]) == 3
    assert {issue["location"] for issue in result["issues"]} == {
        "broken-workspace", "_personal/bad.md", f"{workspace.name}/bad.md",
    }
    assert any("workspace.json" in issue["message"] for issue in result["issues"])


@pytest.mark.parametrize("kind", ["symlink", "junction"])
@pytest.mark.parametrize("name", [".git", ".gitignore", "README.txt"])
def test_root_filter_does_not_skip_alias_validation(local_handoffs, tmp_path, isolated_user_data, kind, name):
    outside = tmp_path / "outside"
    outside.mkdir()
    create_link(isolated_user_data / "handoffs" / name, outside, kind)

    result = run(local_handoffs.list_local())
    assert len(result["handoffs"]) == 3 and len(result["issues"]) == 1
    assert result["issues"][0]["location"] == name
    assert "symlinks or junctions" in result["issues"][0]["message"]


@pytest.mark.parametrize("metadata", [b"broken", b"[]", b"{}", b'{"canonical_path": "relative"}',
    b'{"canonical_path": 1}', b"\xff", None])
def test_bad_workspace_metadata_does_not_hide_valid_entries(local_handoffs, isolated_user_data, metadata):
    directory = isolated_user_data / "handoffs" / "broken"
    directory.mkdir()
    if metadata is not None:
        (directory / "workspace.json").write_bytes(metadata)
    result = run(local_handoffs.list_local())
    assert len(result["handoffs"]) == 3
    assert len(result["issues"]) == 1
    assert "workspace.json" in result["issues"][0]["message"]


@pytest.mark.parametrize("display_name", [None, "Custom project", 4, "", []])
def test_project_display_name_validation(local_handoffs, tmp_path, isolated_user_data, display_name):
    directory = storage_directory(tmp_path / "项目甲", isolated_user_data)
    path = directory / "workspace.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["display_name"] = display_name
    path.write_text(json.dumps(metadata), encoding="utf-8")
    result = run(local_handoffs.list_local())
    if display_name is None or isinstance(display_name, str) and display_name:
        assert not result["issues"]
        assert any(entry["project"]["name"] == (display_name or "项目甲") for entry in result["handoffs"])
    else:
        assert len(result["handoffs"]) == 2 and len(result["issues"]) == 1


@pytest.mark.parametrize("data", [b"broken", b"\xff", b"---\nrevision: 0\n---\n"])
def test_bad_markdown_and_invalid_filename_are_isolated(local_handoffs, isolated_user_data, data):
    directory = personal_storage_directory(isolated_user_data)
    (directory / "bad.md").write_bytes(data)
    (directory / "invalid name.md").write_bytes(data)
    result = run(local_handoffs.list_local())
    assert len(result["handoffs"]) == 3 and len(result["issues"]) == 2
    with pytest.raises(InvalidHandoffError):
        run(local_handoffs.get_local(local_handoffs._local_identity("_personal", "bad")))


@pytest.mark.parametrize("identifier", [None, 1, "", "../design.md", "C:\\secret.md", "zz:design", "0:design",
    "ff:design", "2e2e:design", "2f746d70:design", "433a:design", "612f62:design", "615c62:design",
    "6100:design", "612e:design", "6120:design", "61:../design", "61:a/b", "61:a\\b", "61:" + "a" * 81])
def test_detail_never_accepts_arbitrary_paths(identifier):
    with pytest.raises(InvalidHandoffError):
        run(HandoffStore().get_local(identifier))


def test_detail_missing_file_or_scope(local_handoffs, isolated_user_data):
    entry = next(e for e in run(local_handoffs.list_local())["handoffs"] if e["project"]["scope"] == "personal")
    (personal_storage_directory(isolated_user_data) / "design.md").unlink()
    with pytest.raises(HandoffNotFoundError):
        run(local_handoffs.get_local(entry["id"]))
    with pytest.raises(HandoffNotFoundError):
        run(local_handoffs.get_local(local_handoffs._local_identity("missing", "design")))


@pytest.mark.parametrize("location", ["scope", "file", "base"])
def test_io_errors_are_reported_without_hiding_other_scopes(local_handoffs, isolated_user_data, monkeypatch, location):
    directory = personal_storage_directory(isolated_user_data)
    original = Path.read_bytes if location == "file" else Path.iterdir
    target = directory / "design.md" if location == "file" else directory if location == "scope" else directory.parent
    def fail_one(path):
        if path == target:
            raise PermissionError("injected access denied")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes" if location == "file" else "iterdir", fail_one)
    result = run(local_handoffs.list_local())
    assert len(result["handoffs"]) == (0 if location == "base" else 2)
    assert len(result["issues"]) == 1
    assert "access denied" in result["issues"][0]["message"]


def test_collision_fallback_and_long_unicode_workspace_keys(tmp_path, isolated_user_data):
    store = HandoffStore()
    roots = [tmp_path / "a-b", tmp_path / "a/b", tmp_path / ("项目" * 300)]
    for index, root in enumerate(roots):
        directory = storage_directory(root, isolated_user_data, create=True)
        # Fixtures use the existing Markdown writer, without accessing a project directory.
        store._save(SimpleNamespace(root=root), "same", f"Title {index}", "Summary", f"Body {index}", 0)
        assert directory.is_dir()
    result = run(store.list_local())
    assert not result["issues"] and len(result["handoffs"]) == 3
    assert len({entry["id"] for entry in result["handoffs"]}) == 3
    assert {run(store.get_local(entry["id"]))["content"] for entry in result["handoffs"]} == {"Body 0", "Body 1", "Body 2"}


@pytest.mark.parametrize("kind", ["symlink", "junction"])
@pytest.mark.parametrize("location", ["base", "scope", "metadata", "file"])
def test_browse_rejects_aliases(tmp_path, isolated_user_data, kind, location):
    if kind == "junction" and location in ("metadata", "file"):
        pytest.skip("Junctions are directory links")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_bytes(b"secret")
    base = isolated_user_data / "handoffs"
    if location == "base":
        isolated_user_data.mkdir()
        create_link(base, outside, kind)
        key = "_personal"
    elif location == "scope":
        base.mkdir(parents=True)
        key = "_personal"
        create_link(base / key, outside, kind)
    else:
        directory = storage_directory(tmp_path / "gone", isolated_user_data, create=True)
        key = directory.name
        name = "workspace.json" if location == "metadata" else "design.md"
        if location == "metadata":
            (directory / name).unlink()
        create_link(directory / name, outside / "secret.md", kind)
    store = HandoffStore()
    result = run(store.list_local())
    assert not result["handoffs"] and len(result["issues"]) == 1
    assert "symlinks or junctions" in result["issues"][0]["message"]
    with pytest.raises(InvalidHandoffError, match="symlinks or junctions"):
        run(store.get_local(store._local_identity(key, "design")))


def test_gui_http_share_local_results_without_core(local_handoffs, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "gui/pywebview/python"))
    (tmp_path / "config.yaml").write_text("{}", encoding="utf-8")
    adapter = CoreAdapter(config_path=tmp_path / "config.yaml")
    async def no_runtime(*args):
        pytest.fail("Handoff browsing must not contact Core")
    monkeypatch.setattr(adapter.runtime, "discover", no_runtime)
    bridge = import_module("bridge").GuiBridge(adapter)
    application = create_management_app(adapter, port=18766, stop=lambda: None,
                                         autostart=SimpleNamespace(status=lambda: {}))
    with TestClient(application, base_url="http://127.0.0.1:18766") as client:
        client.headers["X-Chat2Local-CSRF"] = client.get("/api/session").json()["csrf"]
        listing = client.post("/api/list_local_handoffs", json={}).json()
        assert listing == bridge.list_local_handoffs()
        for entry in listing["result"]["handoffs"]:
            result = client.post("/api/get_local_handoff", json={"handoff_id": entry["id"]})
            assert result.json() == bridge.get_local_handoff(entry["id"])
        result = client.post("/api/get_local_handoff", json={"handoff_id": "../secret"})
        assert result.status_code == 400 and result.json() == bridge.get_local_handoff("../secret")
        for method, parameters in (("list_local_handoffs", {}), ("get_local_handoff", {"handoff_id": entry["id"]})):
            for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"},
                            {"X-Chat2Local-CSRF": "wrong"}, {"Sec-Fetch-Site": "cross-site"}):
                assert client.post(f"/api/{method}", json=parameters, headers=headers).status_code == 403
        for parameters in ({"path": "secret.md"}, {"handoff_id": entry["id"], "path": "secret.md"}, {"handoff_id": 1}):
            assert client.post("/api/get_local_handoff", json=parameters).status_code == 400
        assert client.post("/api/list_local_handoffs", json={"workspace": "arbitrary"}).status_code == 400
        for method in ("save_local_handoff", "delete_local_handoff"):
            assert client.post(f"/api/{method}", json={}).status_code == 404
