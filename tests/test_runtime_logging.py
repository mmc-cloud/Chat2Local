"""Real asyncio callback matching, delegation and log output redaction."""

import asyncio
import logging

import pytest
from websockets.asyncio.client import ClientConnection
from websockets.client import ClientProtocol
from websockets.uri import parse_uri

from chat2local.runtime import logging as logging_module
from chat2local.runtime.logging import SafeFormatter, websocket_callback_noise
from conftest import run


def connection():
    return ClientConnection(ClientProtocol(parse_uri("ws://localhost")))


def test_known_noise_from_real_callback_only_is_filtered(caplog):
    async def scenario():
        loop = asyncio.get_running_loop()
        delegated = []
        previous = lambda current, context: delegated.append(context)
        loop.set_exception_handler(previous)
        client = connection()
        with websocket_callback_noise(loop):
            loop.call_soon(client.connection_lost, ConnectionResetError())
            await asyncio.sleep(0)
            assert delegated == []
        assert loop.get_exception_handler() is previous
    caplog.set_level(logging.DEBUG, logger="chat2local.runtime.logging")
    run(scenario())
    assert "Known websockets connection_lost callback noise" in caplog.text
    assert not any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize("kind", ["other-attribute", "same-text", "wrong-origin", "initialized-client", "unknown"])
def test_other_callback_errors_use_default_traceback(caplog, kind):
    async def scenario():
        loop = asyncio.get_running_loop()
        client = connection()
        def unknown():
            if kind == "other-attribute":
                client.unrelated
            elif kind == "same-text":
                raise AttributeError("'ClientConnection' object has no attribute 'recv_messages'")
            elif kind == "wrong-origin":
                client.recv_messages
            elif kind == "initialized-client":
                client.recv_messages = None
                client.connection_lost(None)
            else:
                raise RuntimeError("unrelated callback bug")
        with websocket_callback_noise(loop):
            loop.call_soon(unknown)
            await asyncio.sleep(0)
        assert loop.get_exception_handler() is None
    run(scenario())
    errors = [record for record in caplog.records if record.name == "asyncio"]
    assert len(errors) == 1 and errors[0].exc_info[2] is not None
    assert "Traceback" in caplog.text


def test_existing_handler_receives_unknown_context_unchanged():
    async def scenario():
        loop = asyncio.get_running_loop()
        calls = []
        def previous(current, context):
            calls.append((current, context))
        loop.set_exception_handler(previous)
        context = {"message": "test", "exception": RuntimeError("bug")}
        with websocket_callback_noise(loop):
            loop.call_exception_handler(context)
        assert calls == [(loop, context)]
        assert calls[0][1] is context
        assert loop.get_exception_handler() is previous
    run(scenario())


def test_safe_formatter_preserves_traceback_but_redacts_known_secrets_and_url_auth():
    try:
        raise RuntimeError("TOKEN-SECRET https://alice:URL-PASSWORD@proxy/path")
    except RuntimeError:
        import sys
        record = logging.LogRecord("chat2local", logging.ERROR, __file__, 1, "Unexpected failure", (), sys.exc_info())
    text = SafeFormatter(("TOKEN-SECRET",)).format(record)
    assert "TOKEN-SECRET" not in text and "URL-PASSWORD" not in text and "alice" not in text
    assert "Traceback" in text and "RuntimeError" in text and "Unexpected failure" in text


@pytest.mark.parametrize("debug,level", [(False, logging.INFO), (True, logging.DEBUG)])
def test_logging_setup_uses_stdlib_and_keeps_dependency_payload_logging_disabled(monkeypatch, debug, level):
    captured = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: captured.update(kwargs))
    logging_module.configure_logging(debug=debug, secrets=("TOKEN",))
    assert captured["level"] == logging.INFO
    assert captured["force"] is True
    assert isinstance(captured["handlers"][0].formatter, SafeFormatter)
    assert logging.getLogger("chat2local").level == level
    assert logging.getLogger("websockets").level == logging.WARNING
    assert logging.getLogger("mcp").level == logging.WARNING


def test_real_console_logging_redacts_credentials_with_traceback():
    import subprocess
    import sys
    script = '''
import logging
import io
from chat2local.runtime.logging import configure_logging, SafeFormatter
old_output = io.StringIO()
old_handler = logging.StreamHandler(old_output)
logging.basicConfig(level=logging.DEBUG, handlers=[old_handler])
assert old_handler in logging.getLogger().handlers
configure_logging(debug=True, secrets=("TOKEN-SECRET", "wss://host/path?key=QUERY-SECRET"))
assert old_handler not in logging.getLogger().handlers
assert all(isinstance(h.formatter, SafeFormatter) for h in logging.getLogger().handlers)
try:
    raise TypeError("TOKEN-SECRET http://user:URL-SECRET@proxy:7897 wss://host/path?key=QUERY-SECRET")
except Exception:
    logging.getLogger("chat2local.test").exception("Internal failure")
assert old_output.getvalue() == ""
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "Traceback" in result.stderr and "TypeError" in result.stderr
    assert all(secret not in result.stderr for secret in ("TOKEN-SECRET", "URL-SECRET", "QUERY-SECRET"))
    assert "[redacted]" in result.stderr


@pytest.mark.parametrize("mode", ["hub", "agent", "standalone"])
def test_each_cli_role_replaces_existing_handlers_and_redacts_output(tmp_path, mode):
    import subprocess
    import sys
    config = tmp_path / "config.yaml"
    config.write_text("{}", encoding="utf-8")
    script = '''
import importlib, io, logging, sys
from chat2local.agent.client import AgentClient
from chat2local.runtime.logging import SafeFormatter
entry = importlib.import_module("chat2local.main")
mode, config = sys.argv[1:]
old_output = io.StringIO()
old_handler = logging.StreamHandler(old_output)
logging.basicConfig(level=logging.DEBUG, handlers=[old_handler])
def check():
    assert old_handler not in logging.getLogger().handlers
    assert all(isinstance(h.formatter, SafeFormatter) for h in logging.getLogger().handlers)
    message = "http://user:PROXY-SECRET@localhost:7897"
    if mode != "standalone":
        message = "TOKEN-SECRET " + message
    logging.getLogger("chat2local.test").error(message)
async def agent_run(client):
    check()
AgentClient.run = agent_run
entry.uvicorn.run = lambda *args, **kwargs: check()
sys.argv = ["chat2local", "--config", config, "--device-id", "test"]
if mode != "standalone":
    sys.argv += [mode, "--token", "TOKEN-SECRET"]
if mode == "agent":
    sys.argv += ["--hub-url", "ws://localhost/device/ws"]
entry.main()
assert "TOKEN-SECRET" not in old_output.getvalue()
assert "PROXY-SECRET" not in old_output.getvalue()
'''
    result = subprocess.run([sys.executable, "-c", script, mode, str(config)],
                            capture_output=True, text=True, cwd=tmp_path, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "PROXY-SECRET" not in result.stderr
    # Standalone has no configured Hub token; verify token redaction on network roles.
    if mode != "standalone":
        assert "TOKEN-SECRET" not in result.stderr
    assert "[redacted]" in result.stderr
