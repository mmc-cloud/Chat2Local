"""Real file sinks, UTC output, credential safety, rotation and failure policy."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from chat2local.runtime import logging as logging_module
from chat2local.runtime.logging import SafeFormatter
from chat2local.runtime.supervisor import RuntimeSupervisor
from conftest import run


def file_handler():
    return next(handler for handler in logging.getLogger().handlers
                if isinstance(handler, RotatingFileHandler))


def test_configure_creates_console_and_rotating_file(isolated_user_data, configure_test_logging):
    configure_test_logging()
    console, file = logging.getLogger().handlers
    assert type(console) is logging.StreamHandler
    assert isinstance(file, RotatingFileHandler)
    assert isinstance(console.formatter, SafeFormatter)
    assert isinstance(file.formatter, SafeFormatter)
    assert Path(file.baseFilename) == isolated_user_data / "logs" / "chat2local.log"
    assert file.maxBytes == 5 * 1024 * 1024
    assert file.backupCount == 3
    assert file.encoding == "utf-8"
    assert (isolated_user_data / "logs" / "chat2local.log").is_file()


def test_file_timestamp_is_utc_with_milliseconds(isolated_user_data, configure_test_logging, capsys):
    configure_test_logging()
    record = logging.LogRecord("chat2local.test", logging.INFO, __file__, 1, "日志事件", (), None)
    record.created = 1791171733.128  # 2026-10-05 03:42:13 UTC, independent of host timezone.
    record.msecs = 128
    logging.getLogger().handle(record)
    text = (isolated_user_data / "logs" / "chat2local.log").read_text(encoding="utf-8")
    assert text == "2026-10-05T03:42:13.128Z INFO chat2local.test: 日志事件\n"
    assert capsys.readouterr().err == "INFO chat2local.test: 日志事件\n"


def test_both_sinks_redact_tokens_proxy_urls_and_tracebacks(isolated_user_data, configure_test_logging, capsys):
    configure_test_logging(secrets=("CORE-TOKEN", "HUB-TOKEN", "http://proxy-user:PROXY-PASS@host:7897"))
    logger = logging.getLogger("chat2local.test")
    logger.info("CORE-TOKEN HUB-TOKEN http://proxy-user:PROXY-PASS@host:7897 https://unknown:URL-PASS@host/path")
    try:
        raise RuntimeError("CORE-TOKEN HUB-TOKEN http://proxy-user:PROXY-PASS@host:7897")
    except RuntimeError:
        logger.exception("Unexpected failure")
    file_text = (isolated_user_data / "logs" / "chat2local.log").read_text(encoding="utf-8")
    for text in (capsys.readouterr().err, file_text):
        assert all(secret not in text for secret in ("CORE-TOKEN", "HUB-TOKEN", "PROXY-PASS", "URL-PASS", "proxy-user", "unknown"))
        assert "[redacted]" in text
        assert text.count("Traceback (most recent call last)") == 1
        assert "RuntimeError" in text


def test_rotation_produces_bounded_safe_backups(isolated_user_data, configure_test_logging):
    configure_test_logging(secrets=("TOKEN-SECRET",))
    file_handler().maxBytes = 180
    for index in range(12):
        logging.getLogger("chat2local.test").info("Event %d TOKEN-SECRET http://user:URL-SECRET@proxy 中文", index)
    directory = isolated_user_data / "logs"
    assert {path.name for path in directory.iterdir()} == {
        "chat2local.log", "chat2local.log.1", "chat2local.log.2", "chat2local.log.3",
    }
    for path in directory.iterdir():
        text = path.read_text(encoding="utf-8")
        assert "[redacted]" in text and "中文" in text
        assert "TOKEN-SECRET" not in text and "URL-SECRET" not in text
    assert "Event 11" in (directory / "chat2local.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("failure", ["mkdir", "open"])
def test_initialization_failure_warns_safely_and_core_still_stops(
    tmp_path, monkeypatch, configure_test_logging, capsys, failure,
):
    def fail(*args, **kwargs):
        raise PermissionError("UNCONFIGURED-SECRET http://user:PRIVATE-PASS@host")
    with monkeypatch.context() as patch:
        if failure == "mkdir":
            patch.setattr(Path, "mkdir", fail)
        else:
            patch.setattr(logging_module, "_SafeRotatingFileHandler", fail)
        configure_test_logging()
    assert len(logging.getLogger().handlers) == 1
    supervisor = RuntimeSupervisor("standalone", "test", tmp_path,
                                   state=lambda: "running", directory=tmp_path / "runtime")
    async def core():
        supervisor.request_stop()
    run(supervisor.run(core, lambda task: task.cancel()))
    assert not (tmp_path / "runtime" / "runtime.json").exists()
    text = capsys.readouterr().err
    assert "WARNING" in text and "Could not initialize file logging" in text
    assert "UNCONFIGURED-SECRET" not in text and "PRIVATE-PASS" not in text


@pytest.mark.parametrize("failure", ["write", "rotation"])
def test_sink_failure_never_dumps_raw_record_or_blocks_stop(
    tmp_path, monkeypatch, configure_test_logging, capsys, failure,
):
    configure_test_logging(secrets=("TOKEN-SECRET",))
    handler = file_handler()
    def fail(*args, **kwargs):
        raise OSError("UNCONFIGURED-SECRET http://user:PRIVATE-PASS@host")
    if failure == "write":
        class BrokenStream:
            def write(self, text):
                fail()
            def flush(self):
                pass
            def close(self):
                pass
        handler.stream.close()
        monkeypatch.setattr(handler, "stream", BrokenStream())
        monkeypatch.setattr(handler, "shouldRollover", lambda record: False)
    else:
        monkeypatch.setattr(handler, "shouldRollover", lambda record: True)
        monkeypatch.setattr(handler, "doRollover", fail)
    logging.getLogger("chat2local.test").warning("TOKEN-SECRET http://user:URL-SECRET@proxy")
    supervisor = RuntimeSupervisor("standalone", "test", tmp_path,
                                   state=lambda: "running", directory=tmp_path / "runtime")
    async def core():
        supervisor.request_stop()
    run(supervisor.run(core, lambda task: task.cancel()))
    assert not (tmp_path / "runtime" / "runtime.json").exists()
    text = capsys.readouterr().err
    assert "File logging write/rotation failed" in text
    assert all(secret not in text for secret in ("TOKEN-SECRET", "URL-SECRET", "UNCONFIGURED-SECRET", "PRIVATE-PASS"))
    assert "--- Logging error ---" not in text and "Traceback" not in text


def test_reconfigure_closes_previous_file_and_does_not_duplicate_events(
    isolated_user_data, configure_test_logging,
):
    configure_test_logging()
    previous = file_handler()
    configure_test_logging(debug=True)
    assert previous.stream is None
    assert len(logging.getLogger().handlers) == 2
    logging.getLogger("chat2local.test").info("Single event")
    assert (isolated_user_data / "logs" / "chat2local.log").read_text(encoding="utf-8").count("Single event") == 1
