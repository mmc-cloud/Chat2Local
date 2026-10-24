"""Local browser boundary, adapter reuse, and three-mode HTTP shutdown."""

import asyncio
import json
import socket
from contextlib import ExitStack, contextmanager
from importlib import import_module
from types import SimpleNamespace

import httpx2 as httpx
import pytest
from conftest import run
from fastapi.testclient import TestClient
from test_devices import eventually

from chat2local.agent.client import AgentClient
from chat2local.app import create_app
from chat2local.device.registry import DeviceRegistry
from chat2local.dispatch.local import LocalToolDispatcher
from chat2local.hub.router import DeviceRouter
from chat2local.management import http as management_http
from chat2local.management.adapter import CoreAdapter
from chat2local.management.autostart import StartupCommand
from chat2local.management.http import KEEP_PROXY, MAX_BODY_BYTES, create_management_app
from chat2local.management.runtime import ManagementError, RuntimeClient
from chat2local.runtime import config
from chat2local.runtime.supervisor import RuntimeSupervisor
from chat2local.runtime.workspace import WorkspaceManager

main = import_module("chat2local.main")
ORIGIN = "http://127.0.0.1:18766"


@pytest.fixture
def http_ui(tmp_path, isolated_user_data, monkeypatch):
    config_path = tmp_path / "custom.yaml"
    config_path.write_text("agent:\n  proxy: http://private-user:PRIVATE-PASSWORD@localhost:7897\n")
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "unrelated.yaml")
    adapter = CoreAdapter(RuntimeClient(isolated_user_data), config_path=config_path)
    stopped = []
    autostart = SimpleNamespace(status=lambda: {"enabled": False}, set_enabled=lambda enabled: {"enabled": enabled})
    app = create_management_app(adapter, port=18766, stop=lambda: stopped.append(True), autostart=autostart)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/session").json()["csrf"]
        client.headers["X-Chat2Local-CSRF"] = csrf
        yield client, adapter, stopped


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example:18766"}, {"Host": "localhost:18766"}, {"Host": "127.0.0.1:9999"},
    {"Origin": "https://evil.example"}, {"Origin": "null"},
    {"Origin": "http://127.0.0.1:18767"}, {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"}, {"X-Chat2Local-CSRF": "incorrect"},
])
def test_host_origin_fetch_metadata_and_csrf_reject_before_operations(http_ui, headers):
    client, adapter, stopped = http_ui
    original = adapter.config_path.read_bytes()
    response = client.post("/api/save_config", json={"candidate": {}}, headers=headers)
    assert response.status_code == 403
    assert adapter.config_path.read_bytes() == original and not stopped
    assert "access-control-allow-origin" not in response.headers


def test_session_cannot_be_read_cross_origin_and_form_posts_cannot_mutate(http_ui):
    client, _, stopped = http_ui
    assert client.get("/api/session", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/session", headers={"Host": "evil.example:18766"}).status_code == 403
    assert client.post("/api/stop_core", content="{}", headers={"Content-Type": "text/plain"}).status_code == 403
    del client.headers["X-Chat2Local-CSRF"]
    assert client.post("/api/stop_core", json={}).status_code == 403
    assert client.options("/api/stop_core", headers={"Origin": "https://evil.example"}).status_code == 403
    assert not stopped


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_session_platform_survives_startup_status_errors_and_config_remains_editable(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(management_http, "sys", SimpleNamespace(platform=platform))
    path = tmp_path / "config.yaml"
    config.save_persisted_config({"autostart": {"service": "missing.service", "scope": "system"}}, path)
    calls = []
    def fail_status():
        calls.append("status")
        raise ManagementError("Configured systemd service was not found")
    app = create_management_app(CoreAdapter(config_path=path), port=18766, stop=lambda: None,
                                autostart=SimpleNamespace(status=fail_status))
    with TestClient(app, base_url=ORIGIN) as client:
        session = client.get("/api/session").json()
        assert session["platform"] == platform and not calls
        client.headers["X-Chat2Local-CSRF"] = session["csrf"]
        response = client.post("/api/autostart_status", json={})
        assert response.status_code == 400
        assert response.json() == {"ok": False, "error": {"message": "Configured systemd service was not found"}}
        assert client.get("/api/session").json()["platform"] == platform
        assert calls == ["status"]
        candidate = {"autostart": {"service": "chat2local-core.service", "scope": "user"}}
        for method in ("validate_config", "save_config"):
            assert client.post(f"/api/{method}", json={"candidate": candidate}).json()["ok"]
        assert config.read_persisted_config(path) == candidate
        assert calls == ["status"]  # Editing config never changes the current service.


def test_top_level_link_to_ui_is_safe_but_iframe_and_api_fetches_are_blocked(http_ui):
    client, _, _ = http_ui
    navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    assert client.get("/", headers=navigation).status_code == 200
    assert client.get("/api/session", headers=navigation).status_code == 403
    assert client.get("/", headers={**navigation, "Sec-Fetch-Dest": "iframe"}).status_code == 403


def test_static_resources_security_headers_and_no_public_mcp_or_docs(http_ui):
    client, _, _ = http_ui
    response = client.get("/")
    assert response.status_code == 200
    assert 'name="chat2local-transport" content="http"' in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    for path in ("/mcp/", "/device/ws", "/docs", "/openapi.json", "/assets/../../runtime.json"):
        assert client.get(path).status_code == 404


def test_config_uses_active_path_redacts_secrets_and_preserves_hidden_proxy(http_ui):
    client, adapter, _ = http_ui
    snapshot = client.post("/api/read_config", json={}).json()["result"]
    assert "PRIVATE-PASSWORD" not in json.dumps(snapshot) and "private-user" not in json.dumps(snapshot)
    assert snapshot["persisted"]["agent"]["proxy"] == KEEP_PROXY
    candidate = snapshot["persisted"]
    candidate["read"] = {"max_lines": 17}
    assert client.post("/api/validate_config", json={"candidate": candidate}).json()["ok"]
    saved = client.post("/api/save_config", json={"candidate": candidate})
    assert saved.json()["ok"] and "PRIVATE-PASSWORD" not in saved.text
    assert config.load_config(adapter.config_path).read.max_lines == 17
    assert "PRIVATE-PASSWORD" in config.load_config(adapter.config_path).agent.proxy
    assert not config.DEFAULT_CONFIG_PATH.exists()
    before = adapter.config_path.read_bytes()
    invalid = client.post("/api/save_config", json={"candidate": {"read": {"max_lines": 0}}})
    assert invalid.status_code == 400 and adapter.config_path.read_bytes() == before


def test_strict_requests_size_limit_allowlist_and_no_command_execution(http_ui):
    client, _, stopped = http_ui
    for method in ("exec_command", "__getattribute__", "start_core", "restart_core", "save_desktop_preferences"):
        assert client.post(f"/api/{method}", json={}).status_code == 404
    for method, value in (("set_autostart", {"enabled": "yes"}), ("stop_core", {"unexpected": True}),
                          ("save_config", {"candidate": {"agent": 1}}), ("read_logs", {"cursor": {"offset": -1}})):
        assert client.post(f"/api/{method}", json=value).status_code == 400
    assert client.post("/api/stop_core", content=b"x" * (MAX_BODY_BYTES + 1),
                       headers={"Content-Type": "application/json"}).status_code == 413
    assert not stopped
    assert client.post("/api/stop_core", json={}).json()["result"] == {"accepted": True}
    assert stopped == [True]


def test_credentials_are_returned_only_for_explicit_copy_and_logs_reuse_reader(http_ui):
    client, adapter, _ = http_ui
    info = client.post("/api/manage_token", json={"purpose": "hub"}).json()["result"]
    token = adapter.token_for_copy("hub")
    assert token not in json.dumps(info)
    assert token not in client.post("/api/credential_info", json={"purpose": "hub"}).text
    assert token not in client.post("/api/read_config", json={}).text
    assert client.post("/api/copy_token", json={"purpose": "hub"}).json()["result"]["token"] == token
    adapter.logs.path.parent.mkdir(parents=True)
    adapter.logs.path.write_text("INFO sample log\n", encoding="utf-8")
    assert client.post("/api/read_logs", json={}).json()["result"]["lines"] == ["INFO sample log"]
    assert client.post("/api/set_autostart", json={"enabled": True}).json()["result"]["enabled"]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def reserved_port_block(count):
    """Reserve an isolated contiguous range, unrelated to production ports."""
    for _ in range(30):
        first = free_port()
        if first + count > 65536:
            continue
        with ExitStack() as stack:
            listeners = []
            try:
                for port in range(first, first + count):
                    listener = stack.enter_context(socket.socket())
                    listener.bind(("127.0.0.1", port))
                    listener.listen()
                    listeners.append(listener)
            except OSError:
                continue
            yield first, listeners
            return
    pytest.fail("Could not reserve an isolated port range")


@pytest.mark.parametrize("mode", ["standalone", "hub", "agent"])
def test_default_web_ui_binds_next_available_port_and_reports_it_everywhere(
    tmp_path, monkeypatch, caplog, mode
):
    caplog.set_level("INFO", logger="chat2local.main")
    with reserved_port_block(10) as (first, listeners):
        port = first + 2
        path = tmp_path / "config.yaml"
        path.write_text("{}")

        async def scenario():
            supervisor = RuntimeSupervisor(
                mode, "device", tmp_path, state=lambda: "running"
            )
            # An implicit default in a private range keeps this test off real Core ports.
            settings = config.ManagementConfig.model_construct(
                port=first, _fields_set=set()
            )
            main._attach_management(
                supervisor, settings, path, StartupCommand(("python",), tmp_path)
            )
            assert supervisor.management_ports == tuple(range(first, first + 10))
            start_control = supervisor.control.start

            async def start_control_before_releasing_candidates():
                # Keep every candidate reserved until the real IPC listener has
                # bound port=0, so the OS cannot give it our expected Web UI port.
                control_port = await start_control()
                assert control_port not in supervisor.management_ports
                for listener in listeners[2:]:
                    listener.close()
                return control_port

            monkeypatch.setattr(
                supervisor.control, "start", start_control_before_releasing_candidates
            )

            async def core():
                await asyncio.Event().wait()

            task = asyncio.create_task(
                supervisor.run(core, lambda owner: owner.cancel())
            )
            try:
                await eventually(lambda: supervisor.management.started)
                assert supervisor.management.config.port == port
                assert {
                    sock.getsockname()[1]
                    for server in supervisor.management.servers
                    for sock in server.sockets
                } == {port}
                assert f"Local Web UI: http://127.0.0.1:{port}/" in caplog.text
                assert f"Local Web UI: http://127.0.0.1:{first}/" not in caplog.text
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", trust_env=False
                ) as http:
                    assert (await http.get("/")).status_code == 200
                    http.headers["X-Chat2Local-CSRF"] = (
                        await http.get("/api/session")
                    ).json()["csrf"]
                    http.headers["Origin"] = f"http://127.0.0.1:{port}"
                    for method in ("runtime_status", "read_config"):
                        result = (await http.post(f"/api/{method}", json={})).json()[
                            "result"
                        ]
                        assert result["management_listener"] == {
                            "host": "127.0.0.1",
                            "port": port,
                            "source": "automatic",
                        }
                    assert (
                        await http.post(
                            "/api/read_config",
                            json={},
                            headers={"Host": f"127.0.0.1:{first}"},
                        )
                    ).status_code == 403
                    assert (
                        await http.post(
                            "/api/read_config",
                            json={},
                            headers={"Origin": f"http://127.0.0.1:{first}"},
                        )
                    ).status_code == 403
                    result = await http.post(
                        "/api/save_config",
                        json={"candidate": {"read": {"max_lines": 9}}},
                    )
                    assert (
                        result.json()["result"]["management_listener"]["port"] == port
                    )
                    assert config.read_persisted_config(path) == {
                        "read": {"max_lines": 9}
                    }
            finally:
                supervisor.request_stop()
                await asyncio.wait_for(task, 5)
            with pytest.raises(OSError):
                await asyncio.open_connection("127.0.0.1", port)
            assert not (supervisor.directory / "runtime.json").exists()

        run(scenario())


def test_default_web_ui_range_exhaustion_keeps_core_running_without_more_retries(tmp_path, caplog):
    with reserved_port_block(10) as (first, _):
        path = tmp_path / "config.yaml"
        path.write_text("{}")
        async def scenario():
            supervisor = RuntimeSupervisor("agent", "device", tmp_path, state=lambda: "running")
            settings = config.ManagementConfig.model_construct(port=first, _fields_set=set())
            main._attach_management(supervisor, settings, path, StartupCommand(("python",), tmp_path))
            async def core():
                await asyncio.Event().wait()
            task = asyncio.create_task(supervisor.run(core, lambda owner: owner.cancel()))
            try:
                await eventually(lambda: "All candidate Web UI ports are in use" in caplog.text)
                assert not task.done() and not supervisor.management.started
                assert caplog.text.count("is in use; trying") == 9
                assert (await RuntimeClient(supervisor.directory).status())["core"]["mode"] == "agent"
            finally:
                supervisor.request_stop()
                await asyncio.wait_for(task, 5)
            assert not (supervisor.directory / "runtime.json").exists()
        run(scenario())


@pytest.mark.parametrize("mode", ["standalone", "hub", "agent"])
def test_three_modes_same_process_loopback_api_and_clean_shutdown(tmp_path, monkeypatch, caplog, mode):
    caplog.set_level("INFO", logger="chat2local.main")
    port = free_port()
    config_path = tmp_path / "active.yaml"
    config_path.write_text(f"management:\n  port: {port}\nagent:\n  proxy: direct\n")
    settings = config.load_config(config_path)
    captured = {}
    attach = main._attach_management
    def capture(supervisor, *args, **kwargs):
        attach(supervisor, *args, **kwargs)
        captured["supervisor"] = supervisor
    monkeypatch.setattr(main, "_attach_management", capture)

    async def scenario():
        local = LocalToolDispatcher(WorkspaceManager(tmp_path), settings)
        registry = DeviceRegistry("device") if mode == "hub" else None
        router = DeviceRouter("device", local, registry)
        kwargs = {"management": settings.management, "config_path": config_path,
                  "command": StartupCommand(("python", "-m", "chat2local.main"), tmp_path)}
        if mode == "agent":
            client = AgentClient("ws://127.0.0.1:1/device/ws", "device", "TEST-TOKEN", local, proxy="direct")
            task = asyncio.create_task(main._run_agent(client, **kwargs))
        else:
            application = create_app(router, hub_token="TEST-TOKEN" if mode == "hub" else None)
            task = asyncio.create_task(main._run_server(application, mode=mode, device_id="device",
                                                       workspace=tmp_path, host="127.0.0.1", port=0, **kwargs))
        try:
            await eventually(lambda: captured.get("supervisor") and captured["supervisor"].management.started)
            supervisor = captured["supervisor"]
            web = supervisor.management
            assert f"Local Web UI: http://127.0.0.1:{port}/" in caplog.text
            assert web.config.host == "127.0.0.1" and web.config.proxy_headers is False
            assert {sock.getsockname()[0] for server in web.servers for sock in server.sockets} == {"127.0.0.1"}
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as http:
                assert (await http.get("/")).status_code == 200
                http.headers["X-Chat2Local-CSRF"] = (await http.get("/api/session")).json()["csrf"]
                status = (await http.post("/api/runtime_status", json={})).json()["result"]
                assert status["core"]["mode"] == mode and status["core"]["pid"] == __import__("os").getpid()
                assert status["management_listener"] == {"host": "127.0.0.1", "port": port, "source": "configured"}
                assert "TEST-TOKEN" not in json.dumps(status)
                devices = (await http.post("/api/list_devices", json={})).json()["result"]
                assert devices["mode"] == mode and devices["devices"][0]["device_id"] == "device"
                result = await http.post("/api/save_config", json={"candidate": {"read": {"max_lines": 5}}})
                assert result.json()["result"]["restart_required"]
                assert (await http.post("/api/browser_status", json={})).json()["ok"]
                assert (await http.post("/api/stop_core", json={})).json()["result"]["accepted"]
            await asyncio.wait_for(task, 5)
            assert local.process_manager._closing
            assert not (supervisor.directory / "runtime.json").exists()
            with pytest.raises(OSError):
                await asyncio.open_connection("127.0.0.1", port)
        finally:
            if not task.done():
                captured["supervisor"].request_stop()
                await asyncio.wait_for(task, 5)
    run(scenario())


@pytest.mark.parametrize("mode", ["standalone", "hub", "agent"])
def test_management_bind_failure_keeps_core_running_until_normal_stop(tmp_path, monkeypatch, caplog, mode):
    caplog.set_level("INFO", logger="chat2local.main")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        settings = config.AppConfig()
        local = LocalToolDispatcher(WorkspaceManager(tmp_path), settings)
        path = tmp_path / "config.yaml"
        path.write_text("{}")
        options = {"management": config.ManagementConfig(port=port), "config_path": path,
                   "command": StartupCommand(("python", "-m", "chat2local.main"), tmp_path)}
        captured = {}
        attach = main._attach_management
        def capture(supervisor, *args, **kwargs):
            captured["supervisor"] = supervisor
            attach(supervisor, *args, **kwargs)
        monkeypatch.setattr(main, "_attach_management", capture)
        async def scenario():
            if mode == "agent":
                client = AgentClient("ws://127.0.0.1:1/device/ws", "device", "TEST-TOKEN", local, proxy="direct")
                operation = main._run_agent(client, **options)
            else:
                router = DeviceRouter("device", local, DeviceRegistry("device") if mode == "hub" else None)
                operation = main._run_server(create_app(router, hub_token="TEST-TOKEN" if mode == "hub" else None),
                                             mode=mode, device_id="device", workspace=tmp_path,
                                             host="127.0.0.1", port=0, **options)
            task = asyncio.create_task(operation)
            try:
                await eventually(lambda: "Core continues running" in caplog.text)
                assert not task.done() and not local.process_manager._closing
                assert str(port) in caplog.text
                assert f"Local Web UI: http://127.0.0.1:{port}/" not in caplog.text
                runtime = RuntimeClient(config.user_data_directory())
                assert (await runtime.status())["core"]["mode"] == mode
            finally:
                captured["supervisor"].request_stop()
                await asyncio.wait_for(task, 5)
            assert local.process_manager._closing
            assert not (config.user_data_directory() / "runtime.json").exists()
        run(scenario())


@pytest.mark.parametrize("mode,cli_port,candidate,cli_management,valid", [
    ("standalone", None, {"management": {"port": 8765}}, None, False),
    ("hub", None, {"hub": {"port": 18765}, "management": {"port": 18765}}, None, False),
    ("standalone", None, {"hub": {"port": 18765}, "management": {"port": 18765}}, None, True),
    ("agent", None, {"hub": {"port": 18765}, "management": {"port": 18765}}, None, True),
    ("agent", None, {"management": {"port": 8765}}, None, True),
    ("hub", 18765, {"hub": {"port": 19765}, "management": {"port": 18765}}, None, False),
    ("hub", 19765, {"hub": {"port": 18765}, "management": {"port": 18765}}, None, True),
    ("standalone", 18765, {"management": {"port": 18765}}, None, False),
    ("agent", 18765, {"management": {"port": 18765}}, None, True),
    ("standalone", None, {"management": {"port": 8765}}, {"port": 19766}, False),
    ("hub", None, {"hub": {"port": 8766}}, None, True),
    ("hub", None, {"hub": {"port": 8767}}, None, True),
    ("standalone", 8767, {}, None, True),
    ("agent", 8767, {}, None, True),
    ("hub", None, {"hub": {"port": 8766}, "management": {"enabled": False}}, {"enabled": True, "port": 8766}, False),
])
def test_save_and_validate_share_startup_port_rules(tmp_path, mode, cli_port, candidate, cli_management, valid):
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    app = create_management_app(CoreAdapter(config_path=path), port=18766, stop=lambda: None,
                                autostart=SimpleNamespace(), mode=mode, cli_port=cli_port, cli_management=cli_management)
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers["X-Chat2Local-CSRF"] = client.get("/api/session").json()["csrf"]
        for method in ("validate_config", "save_config"):
            response = client.post(f"/api/{method}", json={"candidate": candidate})
            assert response.status_code == (200 if valid else 400)
            if not valid:
                assert "conflicts" in response.json()["error"]["message"]
                assert path.read_text() == "{}"


@pytest.mark.parametrize("mode", ["standalone", "hub", "agent"])
@pytest.mark.parametrize("browser_port,cli_management,explicit", [(8767, None, False), (8767, None, True), (19766, {"port": 19766}, False)])
def test_browser_conflicts_apply_in_every_mode_including_cli_management(tmp_path, mode, browser_port, cli_management, explicit):
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    app = create_management_app(CoreAdapter(config_path=path), port=18766, stop=lambda: None,
                                autostart=SimpleNamespace(), mode=mode, cli_management=cli_management)
    browser = {"enabled": True, "port": browser_port, "extension_id": "a" * 32, "token_file": "browser.token"}
    candidate = {"browser": browser}
    if explicit:
        candidate["management"] = {"port": browser_port}
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers["X-Chat2Local-CSRF"] = client.get("/api/session").json()["csrf"]
        for method in ("validate_config", "save_config"):
            response = client.post(f"/api/{method}", json={"candidate": candidate})
            if explicit or cli_management:
                assert response.status_code == 400 and "browser.port" in response.text
                assert path.read_text() == "{}"
            else:
                assert response.status_code == 200
    if not explicit and not cli_management:
        assert "management" not in config.read_persisted_config(path)


@pytest.mark.parametrize("saved,port,overrides,source", [
    ({}, 8767, None, "default"),
    ({"hub": {"port": 8766}}, 8767, None, "default"),
    ({"management": {"port": 18766}}, 18766, None, "configured"),
    ({"management": {"port": 19766}}, 18766, {"port": 18766}, "cli"),
])
def test_listener_metadata_separates_runtime_from_persisted_and_default_values(tmp_path, saved, port, overrides, source):
    path = tmp_path / "config.yaml"
    config.save_persisted_config(saved, path)
    adapter = CoreAdapter(config_path=path)
    app = create_management_app(adapter, port=port, stop=lambda: None, autostart=SimpleNamespace(),
                                mode="hub", cli_management=overrides)
    expected = {"host": "127.0.0.1", "port": port, "source": source}
    with TestClient(app, base_url=f"http://127.0.0.1:{port}") as client:
        client.headers["X-Chat2Local-CSRF"] = client.get("/api/session").json()["csrf"]
        for method in ("read_config", "runtime_status"):
            assert client.post(f"/api/{method}", json={}).json()["result"]["management_listener"] == expected
        snapshot = client.post("/api/read_config", json={}).json()["result"]
        assert snapshot["defaults"]["management"]["port"] == 8767
        assert snapshot["persisted"] == saved
        response = client.post("/api/save_config", json={"candidate": {"read": {"max_lines": 9}}})
        assert response.json()["result"]["management_listener"] == expected
        assert config.read_persisted_config(path) == {"read": {"max_lines": 9}}
