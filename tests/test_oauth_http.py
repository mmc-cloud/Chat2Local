"""Real HTTP SDK auth/discovery routes and unchanged Hub WebSocket boundary."""

import json

import httpx2 as httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from websockets.asyncio.client import connect

from chat2local.app import create_app
from chat2local.device.registry import DeviceRegistry
from chat2local.hub.router import DeviceRouter
from chat2local.runtime.config import AuthConfig
from conftest import run
from test_devices import TOKEN, hello, running_server
from test_dispatch import make_dispatcher
from test_workos import ISSUER, RESOURCE, make_token, signing_key, workos


@pytest.fixture(autouse=True)
def unusable_environment_proxy(monkeypatch):
    # Local integration must work even with an unusable proxy and no bypass.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("hub", [False, True])
def test_real_oauth_discovery_auth_and_mcp_tools(tmp_path, workos, signing_key, hub):
    (tmp_path / "file.txt").write_text("authenticated", encoding="utf-8")
    router = DeviceRouter("hub" if hub else "local", make_dispatcher(tmp_path), DeviceRegistry("hub") if hub else None)
    auth = AuthConfig(mode="oauth", provider="workos", issuer_url=ISSUER, resource_server_url=RESOURCE)
    application = create_app(router, hub_token=TOKEN if hub else None, auth=auth)

    async def scenario():
        async with running_server(application) as (url, ws_url):
            async with httpx.AsyncClient(trust_env=False) as client:
                denied = await client.get(url + "/mcp")
                assert denied.status_code == 401  # No redirect to /mcp/.
                assert 'resource_metadata="https://example.com/.well-known/oauth-protected-resource/mcp"' in denied.headers["www-authenticate"]
                assert denied.headers["www-authenticate"].startswith("Bearer ")
                assert (await client.post(url + "/mcp", json={})).status_code == 401
                assert (await client.get(url + "/mcp", headers={"Authorization": "Bearer malformed-private-token"})).status_code == 401
                metadata = await client.get(url + "/.well-known/oauth-protected-resource/mcp")
                assert metadata.status_code == 200
                assert metadata.json()["resource"] == RESOURCE
                assert metadata.json()["authorization_servers"] == [ISSUER]
                assert (await client.get(url + "/mcp/.well-known/oauth-protected-resource/mcp")).status_code == 404
                assert (await client.get(url + "/health")).json() == {"status": "ok"}
                assert (await client.get(url + "/health", headers={"Authorization": "Bearer invalid"})).status_code == 200
                for path in ("/authorize", "/token", "/oauth2/authorize", "/oauth2/token"):
                    assert (await client.get(url + path)).status_code == 404

            token = make_token(signing_key)
            # Exercise external Host/Origin with real SDK auth and JWT verification.
            headers = {"Authorization": f"Bearer {token}", "Host": "example.com", "Origin": "https://example.com"}
            async with httpx.AsyncClient(headers=headers, trust_env=False) as transport, streamable_http_client(url + "/mcp", http_client=transport) as streams:
                async with ClientSession(streams[0], streams[1]) as client:
                    await client.initialize()
                    assert len((await client.list_tools()).tools) == 10
                    result = await client.call_tool("read", {"path": "file.txt"})
                    assert not result.is_error and json.loads(result.content[0].text)["text"] == "authenticated"
            async with httpx.AsyncClient(trust_env=False) as client:
                base = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
                assert (await client.get(url + "/mcp", headers=base | {"Host": "untrusted.example"})).status_code == 421
                assert (await client.get(url + "/mcp", headers=base | {"Origin": "https://untrusted.example"})).status_code == 403

            if hub:
                async with connect(ws_url, proxy=None) as ws:
                    assert not (await hello(ws, token=token)).ok  # JWT cannot replace Hub token.
                async with connect(ws_url, proxy=None) as ws:
                    assert not (await hello(ws, token="invalid-shared-token")).ok
                async with connect(ws_url, proxy=None) as ws:
                    assert (await hello(ws)).ok  # No Authorization header required.
            else:
                async with httpx.AsyncClient(trust_env=False) as client:
                    assert (await client.get(url + "/device/ws")).status_code == 404

    run(scenario())
    assert len(workos[1]["calls"]) == 1


def test_metadata_path_uses_configured_resource_url(tmp_path, workos):
    auth = AuthConfig(mode="oauth", provider="workos", issuer_url=ISSUER,
                      resource_server_url="https://public.example.com/mcp")
    router = DeviceRouter("local", make_dispatcher(tmp_path))

    async def scenario():
        async with running_server(create_app(router, auth=auth)) as (url, _):
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.get(url + "/mcp")
                assert 'resource_metadata="https://public.example.com/.well-known/oauth-protected-resource/mcp"' in response.headers["www-authenticate"]
                metadata = await client.get(url + "/.well-known/oauth-protected-resource/mcp")
                assert metadata.status_code == 200
                assert metadata.json()["resource"] == auth.resource_server_url
    run(scenario())
    assert workos[1]["calls"] == []


@pytest.mark.parametrize("auth", [None, AuthConfig(), AuthConfig(mode="none", provider="workos", issuer_url=ISSUER, resource_server_url=RESOURCE)])
def test_none_mode_preserves_http_behavior_without_jwks(tmp_path, workos, auth):
    router = DeviceRouter("local", make_dispatcher(tmp_path))

    async def scenario():
        async with running_server(create_app(router, auth=auth)) as (url, _):
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.get(url + "/mcp", follow_redirects=True)
                assert response.status_code != 401
                assert "www-authenticate" not in response.headers
                assert (await client.get(url + "/.well-known/oauth-protected-resource/mcp")).status_code == 404
            async with httpx.AsyncClient(trust_env=False) as transport, streamable_http_client(url + "/mcp/", http_client=transport) as streams:
                async with ClientSession(streams[0], streams[1]) as client:
                    await client.initialize()
                    assert len((await client.list_tools()).tools) == 10
    run(scenario())
    assert workos[1]["calls"] == []
