"""Loopback-only HTTP transport for the existing Core management adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
import hmac
import inspect
import logging
from pathlib import Path
import secrets
import sys
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.background import BackgroundTask

from chat2local.management.adapter import CoreAdapter
from chat2local.management.autostart import CoreAutostart
from chat2local.management.logs import LogCursor
from chat2local.management.runtime import ManagementError
from chat2local.runtime import config
from chat2local.runtime.workspace import WorkspaceError

logger = logging.getLogger(__name__)
STATIC_DIRECTORY = Path(__file__).with_name("static")
MAX_BODY_BYTES = 256 * 1024
KEEP_PROXY = "__CHAT2LOCAL_KEEP_PROXY__"


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": {"message": message}}, status_code=status)


class LocalBrowserBoundary:
    """Validate browser authority before either static files or API handlers run."""

    def __init__(self, app, *, port: Callable[[], int], csrf: str) -> None:
        self.app, self.csrf = app, csrf
        self.port = port

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return
        authority = f"127.0.0.1:{self.port()}"
        origin = f"http://{authority}"
        headers = {}
        for key, value in scope["headers"]:
            if key in headers and key in (b"host", b"origin", b"x-chat2local-csrf"):
                await _error("Invalid management request", 403)(scope, receive, send)
                return
            headers[key] = value.decode("latin-1")
        problem = None
        if headers.get(b"host") != authority:
            problem = "Invalid management Host"
        elif b"origin" in headers and headers[b"origin"] != origin:
            problem = "Cross-origin management requests are forbidden"
        elif headers.get(b"sec-fetch-site", "none") not in ("same-origin", "none") and not (
            scope["method"] == "GET" and scope["path"] == "/"
            and headers.get(b"sec-fetch-mode") == "navigate" and headers.get(b"sec-fetch-dest") == "document"
        ):
            problem = "Cross-origin management requests are forbidden"
        elif scope["path"].startswith("/api/") and scope["path"] != "/api/session":
            if not hmac.compare_digest(headers.get(b"x-chat2local-csrf", "").encode(), self.csrf.encode()):
                problem = "Invalid management CSRF token; reload the page"
            elif headers.get(b"content-type", "").split(";", 1)[0].strip() != "application/json":
                problem = "Management requests require application/json"
        if problem:
            await _error(problem, 403)(scope, receive, send)
            return

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []),
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"), (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'")]
            await send(message)

        # Bound all bodies, including chunked bodies with no Content-Length.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_BODY_BYTES:
                await _error("Management request is too large", 413)(scope, receive, secure_send)
                return
            if not message.get("more_body", False):
                break
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, bounded_receive, secure_send)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ConfigRequest(_Request):
    candidate: dict


class CredentialRequest(_Request):
    purpose: Literal["browser", "hub", "agent"]
    path: str | None = None


class TokenRequest(CredentialRequest):
    value: str | None = None
    regenerate: bool = False
    confirmed: bool = False


class LogsRequest(_Request):
    cursor: LogCursor | None = None


class AutostartRequest(_Request):
    enabled: bool


class HandoffRequest(_Request):
    handoff_id: str


def _redact_snapshot(snapshot: dict) -> dict:
    snapshot = deepcopy(snapshot)
    for part in ("persisted", "effective"):
        proxy = snapshot.get(part, {}).get("agent", {}).get("proxy")
        if isinstance(proxy, str) and urlsplit(proxy).username is not None:
            snapshot[part]["agent"]["proxy"] = KEEP_PROXY
    return snapshot


def create_management_app(adapter: CoreAdapter, *, port: int, stop: Callable[[], None],
                          autostart: CoreAutostart, static_directory: Path = STATIC_DIRECTORY,
                          mode: str = "standalone", cli_port: int | None = None,
                          cli_management: dict | None = None,
                          listener_port: Callable[[], int] | None = None) -> FastAPI:
    application = FastAPI(title="Chat2Local Management", docs_url=None, redoc_url=None, openapi_url=None)
    csrf = secrets.token_urlsafe(32)
    actual_port = listener_port if listener_port is not None else lambda: port
    application.add_middleware(LocalBrowserBoundary, port=actual_port, csrf=csrf)
    overrides = {key: value for key, value in (cli_management or {}).items() if value is not None}
    saved_management = config.load_config(adapter.config_path).management
    source = "cli" if "port" in overrides else "configured" if "port" in saved_management.model_fields_set else "default"
    def listener():
        current = actual_port()
        return {"host": "127.0.0.1", "port": current,
                "source": "automatic" if source == "default" and current != port else source}

    async def runtime_status(_):
        return {**await adapter.runtime_status(), "management_listener": listener()}

    def read_config():
        return {**_redact_snapshot(adapter.read_config()), "management_listener": listener()}

    @application.get("/api/session")
    async def session():
        from importlib.metadata import version
        return {"ok": True, "app": "Chat2Local", "version": version("chat2local"),
                "backend": "http", "platform": sys.platform, "csrf": csrf}

    def candidate(request: ConfigRequest) -> dict:
        value = deepcopy(request.candidate)
        agent = value.get("agent")
        if isinstance(agent, dict) and agent.get("proxy") == KEEP_PROXY:
            previous = config.read_persisted_config(adapter.config_path).get("agent", {}).get("proxy")
            if not isinstance(previous, str) or urlsplit(previous).username is None:
                raise ManagementError("Proxy credentials changed; reload configuration")
            value["agent"]["proxy"] = previous
        settings = config.validate_persisted_config(value)
        # A temporary CLI override must not hide an invalid explicit saved port.
        config.management_ports(settings, mode=mode, cli_port=cli_port)
        if overrides:
            effective = settings.model_copy(update={"management": settings.management.model_copy(update=overrides)})
            config.management_ports(effective, mode=mode, cli_port=cli_port)
        return value

    async def save(request):
        return {**_redact_snapshot(await adapter.save_config(candidate(request))), "management_listener": listener()}

    # Explicit transport allowlist. No generic attribute/command invocation.
    operations = {
        "runtime_status": (_Request, runtime_status),
        "read_config": (_Request, lambda _: read_config()),
        "validate_config": (ConfigRequest, lambda req: adapter.validate_config(candidate(req))),
        "save_config": (ConfigRequest, save),
        "read_logs": (LogsRequest, lambda req: adapter.logs.read(req.cursor.model_dump() if req.cursor else None)),
        "list_devices": (_Request, lambda _: adapter.runtime.devices()),
        "list_local_handoffs": (_Request, lambda _: adapter.list_local_handoffs()),
        "get_local_handoff": (HandoffRequest, lambda req: adapter.get_local_handoff(req.handoff_id)),
        "browser_status": (_Request, lambda _: adapter.browser_status()),
        "credential_info": (CredentialRequest, lambda req: adapter.credential_info(req.purpose, req.path)),
        "manage_token": (TokenRequest, lambda req: adapter.manage_token(**req.model_dump())),
        # Only this explicit, CSRF-protected action returns a token for the clipboard.
        "copy_token": (CredentialRequest, lambda req: {"token": adapter.token_for_copy(req.purpose, req.path)}),
        "autostart_status": (_Request, lambda _: autostart.status()),
        "set_autostart": (AutostartRequest, lambda req: autostart.set_enabled(req.enabled)),
    }

    async def stop_after_response():
        stop()

    @application.post("/api/{method}")
    async def invoke(method: str, request: Request):
        if method not in operations and method != "stop_core":
            return _error("Unknown management operation", 404)
        try:
            raw = await request.json()
            if method == "stop_core":
                _Request.model_validate(raw)
                return JSONResponse({"ok": True, "result": {"accepted": True}},
                                    background=BackgroundTask(stop_after_response))
            model, operation = operations[method]
            parameters = model.model_validate(raw)
            # Disk and OS service calls must not block Agent/Hub traffic.
            if inspect.iscoroutinefunction(operation):
                result = await operation(parameters)
            else:
                result = await asyncio.to_thread(operation, parameters)
                if inspect.isawaitable(result):
                    result = await result
            return {"ok": True, "result": result}
        except (ValidationError, ValueError) as error:
            if isinstance(error, config.ConfigError):
                return _error(str(error))
            return _error("Invalid management request")
        except (ManagementError, WorkspaceError) as error:
            return _error(str(error))
        except Exception:
            logger.error("Management operation failed: %s", method)
            return _error("Internal management error; see logs", 500)

    @application.get("/")
    async def index():
        if not (static_directory / "index.html").is_file():
            return _error("Web UI assets are missing; build the shared frontend before packaging", 503)
        return FileResponse(static_directory / "index.html")

    if (static_directory / "assets").is_dir():
        application.mount("/assets", StaticFiles(directory=static_directory / "assets"), name="assets")
    return application
