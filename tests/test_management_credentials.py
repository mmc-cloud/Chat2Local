"""Credential operations use only isolated private files; never real tokens or clipboard."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from chat2local.management.credentials import CredentialStore
from chat2local.management.adapter import CoreAdapter
from chat2local.management.runtime import ManagementError, RuntimeClient
from chat2local.runtime import config
from conftest import run


@pytest.fixture
def store(isolated_user_data, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", isolated_user_data / "config.yaml")
    return CredentialStore()


def test_generate_reuse_and_explicit_rotation(store, isolated_user_data):
    result = store.write("browser")
    path = Path(result["path"])
    secret = store.read("browser")
    assert len(secret) >= 43 and path == isolated_user_data / "browser.token"
    assert result["state"] == "ready" and not result["reused"]
    before = path.stat().st_mtime_ns
    assert store.write("browser")["reused"]
    assert path.stat().st_mtime_ns == before and store.read("browser") == secret
    assert secret not in json.dumps(store.info("browser"))
    with pytest.raises(ManagementError, match="confirmation"):
        store.write("browser", regenerate=True)
    assert store.read("browser") == secret
    store.write("browser", regenerate=True, confirmed=True)
    assert store.read("browser") != secret
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_agent_import_keeps_existing_shared_token(store):
    with pytest.raises(ManagementError, match="Agent requires"):
        store.write("agent")
    secret = "existing-remote-hub-token"
    result = store.write("agent", value=secret)
    assert store.read("agent") == secret and not result["reused"]
    assert store.write("agent", value=secret)["reused"]
    with pytest.raises(ManagementError, match="already exists"):
        store.write("agent", value="different")
    assert store.read("agent") == secret
    with pytest.raises(ManagementError):
        store.write("hub", regenerate=True, confirmed=True)


def test_browser_cannot_reuse_hub_path_or_value(store, monkeypatch):
    hub = store.write("hub")
    with pytest.raises(ManagementError, match="independent"):
        store.write("browser", hub["path"])
    shared = store.read("hub")
    with pytest.raises(ManagementError, match="independent"):
        store.write("browser", value=shared)
    monkeypatch.setenv("CHAT2LOCAL_HUB_TOKEN", "e" * 43)
    with pytest.raises(ManagementError, match="independent"):
        store.write("browser", value="e" * 43)


@pytest.mark.parametrize("value", ["", "\n", "line\nbreak", "x" * 4097, "\x00secret"])
def test_invalid_import_never_creates_a_file(store, value):
    with pytest.raises(ManagementError):
        store.write("agent", value=value)
    assert store.info("agent")["state"] == "missing"


def test_no_overwrite_when_existing_file_is_invalid(store, isolated_user_data):
    path = isolated_user_data / "browser.token"
    path.parent.mkdir()
    path.write_bytes(b"invalid-token")
    with pytest.raises(ManagementError):
        store.write("browser")
    assert path.read_bytes() == b"invalid-token"


def test_private_path_boundary_and_repository_guard(store, isolated_user_data, tmp_path):
    with pytest.raises(ManagementError, match="private user"):
        store.write("hub", str(tmp_path / "outside.token"))
    with pytest.raises(ManagementError, match="absolute"):
        store.write("hub", "relative.token")
    isolated_user_data.mkdir()
    (isolated_user_data / ".git").mkdir()
    with pytest.raises(ManagementError, match="Git repository"):
        store.write("hub")


def test_concurrent_generation_reuses_one_complete_secret(store):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: store.write("browser"), range(12)))
    assert sum(not item["reused"] for item in results) == 1
    assert store.info("browser")["state"] == "ready"


def test_rotation_failure_preserves_old_file_and_redacts_error(store, monkeypatch, caplog):
    path = Path(store.write("browser")["path"])
    old = path.read_bytes()
    def denied(*args):
        raise OSError("PRIVATE-TOKEN-EXCEPTION")
    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(ManagementError) as failure:
        store.write("browser", regenerate=True, confirmed=True)
    assert path.read_bytes() == old
    assert not list(path.parent.glob(".credential-*"))
    assert "PRIVATE" not in str(failure.value) + caplog.text


def test_links_and_hardlinks_are_rejected(store, isolated_user_data, tmp_path):
    path = Path(store.write("hub")["path"])
    linked = isolated_user_data / "linked.token"
    os.link(path, linked)
    with pytest.raises(ManagementError):
        store.read("hub", str(linked))
    linked.unlink()
    try:
        linked.symlink_to(path)
    except OSError:
        pytest.skip("Symlink creation is not permitted on this Windows host")
    with pytest.raises(ManagementError):
        store.read("hub", str(linked))


def test_gui_copy_never_returns_plaintext(store, monkeypatch, caplog):
    from importlib import import_module
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "gui/pywebview/python"))
    module = import_module("bridge")
    adapter = CoreAdapter(RuntimeClient(config.user_data_directory()))
    adapter.manage_token("browser")
    secret = adapter.token_for_copy("browser")
    copied = []
    monkeypatch.setattr(module, "copy_text", lambda text, window: copied.append(text))
    gui = module.GuiBridge(adapter)
    assert gui.copy_token("browser") == {"ok": True, "result": {"copied": True}}
    assert copied == [secret]
    snapshots = [gui.read_config(), gui.credential_info("browser"), gui.manage_token("browser")]
    assert secret not in json.dumps(snapshots) + caplog.text
    def unavailable(text, window):
        raise RuntimeError(text)
    monkeypatch.setattr(module, "copy_text", unavailable)
    assert secret not in json.dumps(gui.copy_token("browser")) + caplog.text


def test_normal_config_actions_do_not_create_tokens_and_shared_browser_config_is_rejected(store):
    adapter = CoreAdapter(RuntimeClient(config.user_data_directory()))
    adapter.read_config()
    run(adapter.save_config({"browser": {"enabled": False}, "read": {"max_lines": 19}}))
    assert not list(config.user_data_directory().glob("*.token"))
    shared = store.write("hub")
    with pytest.raises(ManagementError, match="independent"):
        adapter.validate_config({"browser": {"token_file": shared["path"]}, "hub": {"token_file": shared["path"]}})


def test_browser_gui_save_preserves_other_sparse_config_and_files(store):
    adapter = CoreAdapter(RuntimeClient(config.user_data_directory()))
    created = adapter.manage_token("browser")
    path = Path(created["path"])
    original = path.read_bytes()
    candidate = {"browser": {"enabled": True, "port": 8766, "extension_id": "a" * 32, "token_file": str(path)},
                 "agent": {"hub_url": "wss://hub.example/device/ws"}, "read": {"max_lines": 17}}
    result = run(adapter.save_config(candidate))
    assert result["persisted"] == candidate
    assert adapter.read_config()["persisted"] == candidate
    assert path.read_bytes() == original


@pytest.mark.parametrize("state", ["disabled", "listening", "connected", "failed", "starting"])
def test_adapter_browser_status_uses_live_management_data(store, state):
    async def browser():
        return {"state": state, "config": config.BrowserConfig().model_dump(), "credential_current": True}
    adapter = CoreAdapter(SimpleNamespace(directory=config.user_data_directory(), browser=browser))
    assert run(adapter.browser_status()) == {"state": state}
    async def changed():
        return {"state": state, "config": {"enabled": True}, "credential_current": True}
    adapter.runtime.browser = changed
    assert run(adapter.browser_status()) == {"state": "restart_required"}
    async def rotated():
        return {"state": state, "config": config.BrowserConfig().model_dump(), "credential_current": False}
    adapter.runtime.browser = rotated
    assert run(adapter.browser_status()) == {"state": "restart_required"}
