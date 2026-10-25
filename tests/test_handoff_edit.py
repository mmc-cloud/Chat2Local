"""Existing-only local saves share validation and atomic writes with MCP saves."""

import asyncio
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from chat2local.handoff import store as module
from chat2local.handoff.models import HandoffNotFoundError, InvalidHandoffError, RevisionConflictError
from chat2local.handoff.store import HandoffStore
from chat2local.management.adapter import CoreAdapter
from chat2local.management.http import MAX_BODY_BYTES, create_management_app
from conftest import run
from test_handoff_browse import local_handoffs  # noqa: F401 -- shared isolated fixture
from test_handoff_store import INVALID_DISCOVERY


def record_path(store, entry):
    return store._user_data / "handoffs" / entry["project"]["key"] / f"{entry['workstream']}.md"


@pytest.mark.parametrize("scope", ["workspace", "personal"])
def test_save_local_round_trip_and_revision(local_handoffs, scope):
    store = local_handoffs
    entry = next(e for e in run(store.list_local())["handoffs"] if e["project"]["scope"] == scope)
    if scope == "workspace":
        assert not Path(entry["project"]["path"]).exists()
    saved = run(store.save_local(entry["id"], "新标题 🚀", "新摘要", "# 新内容\r\n正文\r尾行", 1))
    assert saved == {**entry, "title": "新标题 🚀", "summary": "新摘要", "content": "# 新内容\n正文\n尾行",
                     "revision": 2, "updated_at": saved["updated_at"]}
    assert saved["updated_at"] != entry["updated_at"]
    assert run(store.get_local(entry["id"])) == saved
    listing = next(e for e in run(store.list_local())["handoffs"] if e["id"] == entry["id"])
    assert listing == {k: v for k, v in saved.items() if k != "content"}


@pytest.mark.parametrize("scope", ["workspace", "personal"])
def test_mcp_update_conflicts_with_local_draft_without_overwrite(local_handoffs, scope):
    store = local_handoffs
    entry = next(e for e in run(store.list_local())["handoffs"] if e["project"]["scope"] == scope)
    workspace = SimpleNamespace(root=Path(entry["project"]["path"])) if scope == "workspace" else None
    run(store.save(workspace, entry["workstream"], "MCP title", "MCP summary", "MCP body", 1))
    path = record_path(store, entry)
    before = path.read_bytes()
    with pytest.raises(RevisionConflictError, match="expected revision 1, current revision 2"):
        run(store.save_local(entry["id"], "Draft title", "Draft summary", "Draft body", 1))
    assert path.read_bytes() == before
    assert run(store.get_local(entry["id"]))["content"] == "MCP body"


def test_concurrent_local_saves_on_same_store_have_one_winner(local_handoffs):
    store = local_handoffs
    entry = run(store.list_local())["handoffs"][0]
    async def scenario():
        results = await asyncio.gather(
            store.save_local(entry["id"], "First", "Summary", "First body", 1),
            store.save_local(entry["id"], "Second", "Summary", "Second body", 1),
            return_exceptions=True,
        )
        assert sum(isinstance(result, dict) for result in results) == 1
        assert sum(isinstance(result, RevisionConflictError) for result in results) == 1
        winner = next(result for result in results if isinstance(result, dict))
        assert winner["revision"] == 2
        assert await store.get_local(entry["id"]) == winner
    run(scenario())


@pytest.mark.parametrize("state", ["deleted", "corrupt", "missing-scope"])
def test_local_save_never_creates_or_repairs_records(local_handoffs, state):
    store = local_handoffs
    entry = next(e for e in run(store.list_local())["handoffs"] if e["project"]["scope"] == "personal")
    path = record_path(store, entry)
    if state == "corrupt":
        path.write_bytes(b"broken")
    else:
        path.unlink()
        if state == "missing-scope":
            path.parent.rmdir()
    with pytest.raises(InvalidHandoffError if state == "corrupt" else HandoffNotFoundError):
        run(store.save_local(entry["id"], "Title", "Summary", "body", 0))
    assert path.read_bytes() == b"broken" if state == "corrupt" else not path.exists()
    assert not list(path.parent.glob(".handoff-*"))


def test_local_save_rejects_damaged_workspace_ownership(local_handoffs):
    store = local_handoffs
    entry = next(e for e in run(store.list_local())["handoffs"] if e["project"]["scope"] == "workspace")
    path = record_path(store, entry)
    original = path.read_bytes()
    (path.parent / "workspace.json").write_bytes(b"broken")
    with pytest.raises(InvalidHandoffError, match="workspace.json"):
        run(store.save_local(entry["id"], "Title", "Summary", "body", 1))
    assert path.read_bytes() == original


@pytest.mark.parametrize("identifier", [None, 1, "../file.md", "2e2e:design", "2f746d70:design", "433a:design", "ff:design", "61:../x"])
def test_local_save_rejects_invalid_identifiers(identifier, isolated_user_data):
    with pytest.raises(InvalidHandoffError):
        run(HandoffStore().save_local(identifier, "Title", "Summary", "body", 1))
    assert not isolated_user_data.exists()


@pytest.mark.parametrize("field,value", [*INVALID_DISCOVERY,
    *[("expected_revision", value) for value in (True, "1", 1.0, -1, None)],
    ("content", None), ("content", 1)])
def test_local_save_reuses_strict_validation(local_handoffs, field, value):
    entry = run(local_handoffs.list_local())["handoffs"][0]
    path = record_path(local_handoffs, entry)
    before = path.read_bytes()
    arguments = dict(handoff_id=entry["id"], title="Title", summary="Summary", content="body", expected_revision=1)
    arguments[field] = value
    with pytest.raises(ValueError):
        run(local_handoffs.save_local(**arguments))
    assert path.read_bytes() == before


@pytest.mark.parametrize("failure", ["temp", "replace", "deleted", "changed", "corrupt"])
def test_write_failures_and_changes_during_save_preserve_disk(local_handoffs, monkeypatch, failure):
    store = local_handoffs
    entry = run(store.list_local())["handoffs"][0]
    path = record_path(store, entry)
    before = path.read_bytes()
    def fail(*args, **kwargs):
        raise PermissionError("injected failure")
    if failure == "temp":
        monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", fail)
    elif failure == "replace":
        monkeypatch.setattr(module.os, "replace", fail)
    else:
        original_temp = module.tempfile.NamedTemporaryFile
        def change_before_write(*args, **kwargs):
            if failure == "deleted":
                path.unlink()
            elif failure == "changed":
                path.write_bytes(before.replace(b"revision: 1", b"revision: 2"))
            else:
                path.write_bytes(b"broken")
            return original_temp(*args, **kwargs)
        monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", change_before_write)
    expected_error = {"deleted": HandoffNotFoundError, "changed": RevisionConflictError, "corrupt": InvalidHandoffError}.get(failure, OSError)
    with pytest.raises(expected_error):
        run(store.save_local(entry["id"], "Draft", "Summary", "Draft body", 1))
    expected = before.replace(b"revision: 1", b"revision: 2") if failure == "changed" else b"broken" if failure == "corrupt" else before
    assert not path.exists() if failure == "deleted" else path.read_bytes() == expected
    assert not list(path.parent.glob(".handoff-*"))


def test_gui_http_saves_and_failures_have_same_results(local_handoffs, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "gui/pywebview/python"))
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}", encoding="utf-8")
    adapter = CoreAdapter(config_path=config_path)
    bridge = import_module("bridge").GuiBridge(adapter)
    entry = run(local_handoffs.list_local())["handoffs"][0]
    parameters = {"handoff_id": entry["id"], "title": "Edited", "summary": "New summary", "content": "New body", "expected_revision": 1}
    app = create_management_app(adapter, port=18766, stop=lambda: None, autostart=SimpleNamespace(status=lambda: {}))
    with TestClient(app, base_url="http://127.0.0.1:18766") as client:
        client.headers["X-Chat2Local-CSRF"] = client.get("/api/session").json()["csrf"]
        for use_gui in (True, False):
            if use_gui:
                response = bridge.save_local_handoff(**parameters)
            else:
                response = client.post("/api/save_local_handoff", json=parameters).json()
            assert response["ok"] and response["result"]["revision"] == parameters["expected_revision"] + 1
            assert response == bridge.get_local_handoff(entry["id"])
            assert client.post("/api/get_local_handoff", json={"handoff_id": entry["id"]}).json() == response
            parameters["expected_revision"] += 1
        parameters["expected_revision"] = 1
        response = client.post("/api/save_local_handoff", json=parameters)
        assert response.status_code == 400 and response.json() == bridge.save_local_handoff(**parameters)
        assert response.json()["error"]["message"].startswith("revision_conflict:")
        before = record_path(local_handoffs, entry).read_bytes()
        for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"},
                        {"X-Chat2Local-CSRF": "wrong"}, {"Sec-Fetch-Site": "cross-site"}):
            assert client.post("/api/save_local_handoff", json=parameters, headers=headers).status_code == 403
        for fields in ({"expected_revision": True}, {"expected_revision": "3"}, {"title": 1}, {"content": None}, {"path": "secret.md"}):
            assert client.post("/api/save_local_handoff", json={**parameters, **fields}).status_code == 400
        assert client.post("/api/save_local_handoff", content=b"x" * (MAX_BODY_BYTES + 1), headers={"Content-Type": "application/json"}).status_code == 413
        assert record_path(local_handoffs, entry).read_bytes() == before
