"""Sparse persisted config, safe validation and atomic replacement failures."""

from collections import UserDict
from contextlib import contextmanager
from pathlib import Path
import traceback

import pytest
import yaml

from chat2local.runtime import config as config_module
from chat2local.runtime.config import (
    AppConfig, ConfigError, load_config, read_persisted_config,
    save_persisted_config, validate_persisted_config,
)


@pytest.fixture
def config_path(isolated_user_data, monkeypatch):
    path = isolated_user_data / "config.yaml"
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", path)
    return path


def test_missing_default_reads_empty_without_creating_anything(config_path):
    assert read_persisted_config() == {}
    assert load_config() == AppConfig()
    assert not config_path.parent.exists()


def test_read_preserves_sparse_fields_without_schema_defaults(config_path):
    config_path.parent.mkdir()
    config_path.write_text("agent:\n  hub_url: wss://example.com/device/ws\n", encoding="utf-8")
    assert read_persisted_config() == {"agent": {"hub_url": "wss://example.com/device/ws"}}
    assert load_config().agent.proxy == "system"


@pytest.mark.parametrize("content", [
    b"agent: [http://user:PRIVATE-PASSWORD@host\n",  # YAML parser exceptions contain input.
    b"- PRIVATE-PASSWORD\n",
    b"agent: PRIVATE-PASSWORD\xff",  # Decoder exception representations contain input.
])
def test_read_errors_do_not_echo_sensitive_content(config_path, content, caplog):
    config_path.parent.mkdir()
    config_path.write_bytes(content)
    with pytest.raises(ConfigError) as failure:
        read_persisted_config()
    diagnostic = "".join(traceback.format_exception(failure.value))
    assert "PRIVATE-PASSWORD" not in diagnostic and "PRIVATE-PASSWORD" not in caplog.text
    assert config_path.read_bytes() == content


@pytest.mark.parametrize("content", ["", "# No explicit configuration\n", "{}\n"])
def test_empty_yaml_reads_as_empty_mapping(config_path, content):
    config_path.parent.mkdir()
    config_path.write_text(content, encoding="utf-8")
    assert read_persisted_config() == {}


def test_explicit_missing_path_stays_an_error(tmp_path):
    for read in (read_persisted_config, load_config):
        with pytest.raises(ConfigError, match="Config file does not exist"):
            read(tmp_path / "missing.yaml")


def test_read_returns_written_mapping_even_if_schema_is_invalid(config_path):
    config_path.parent.mkdir()
    config_path.write_text("read:\n  max_lines: 0\n", encoding="utf-8")
    candidate = read_persisted_config()
    assert candidate == {"read": {"max_lines": 0}}
    with pytest.raises(ConfigError):
        validate_persisted_config(candidate)


def test_validate_uses_existing_schema_and_retains_explicit_fields():
    candidate = UserDict({"agent": {"proxy": "system", "device_id": None}, "read": {"max_lines": 12}})
    validated = validate_persisted_config(candidate)
    assert isinstance(validated, AppConfig)
    assert validated.read.max_bytes == AppConfig().read.max_bytes
    assert validated.model_dump(exclude_unset=True) == candidate
    assert candidate == {"agent": {"proxy": "system", "device_id": None}, "read": {"max_lines": 12}}


@pytest.mark.parametrize("candidate,field", [
    ({"read": {"max_lines": 0}}, "read.max_lines"),
    ({"process": {"foreground_timeout": float("inf")}}, "process.foreground_timeout"),
    ({"read": {"max_lines": "PRIVATE-PASSWORD"}}, "read.max_lines"),
    ({"agent": {"proxy": "ftp://user:PRIVATE-PASSWORD@host"}}, "agent.proxy"),
    ({"auth": {"mode": "oauth"}}, "auth.provider"),
    ({"auth": {"issuer_url": "https://user:PRIVATE-PASSWORD@host"}}, "auth.issuer_url"),
    ({"unknown": "PRIVATE-PASSWORD"}, "unknown"),
    ({"agent": {"token": "PRIVATE-PASSWORD"}}, "agent.token"),
    ({"workspace": "PRIVATE-PASSWORD"}, "workspace"),
    ({"default_workspace": "PRIVATE-PASSWORD"}, "default_workspace"),
])
def test_invalid_candidate_has_safe_field_diagnostic(candidate, field, caplog):
    with pytest.raises(ConfigError) as failure:
        validate_persisted_config(candidate)
    assert field in str(failure.value)
    assert "PRIVATE-PASSWORD" not in "".join(traceback.format_exception(failure.value))
    assert caplog.records == []


@pytest.mark.parametrize("candidate", [None, [], "PRIVATE-PASSWORD", AppConfig()])
def test_effective_models_and_non_mappings_cannot_be_persisted(config_path, candidate):
    with pytest.raises(ConfigError, match="expected a mapping"):
        save_persisted_config(candidate)
    assert not config_path.parent.exists()


@pytest.mark.parametrize("candidate", [
    {},
    {"agent": {"hub_url": "wss://example.com/device/ws"}},
    {"agent": {"proxy": "system"}},
    {"hub": {"host": None}, "agent": {"token_file": None}},
    {"agent": {}},
    {"read": {"max_lines": 1000}, "search": {"timeout": 10.0}},
    {"agent": {"device_id": "中文设备😀", "token_file": "~/.chat2local/令牌"},
     "security": {"allowed_roots": ["D:\\中文项目", "/home/中文项目"]}},
])
def test_save_round_trips_only_explicit_fields(config_path, candidate, caplog):
    save_persisted_config(candidate)
    assert read_persisted_config() == candidate
    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == candidate
    assert validate_persisted_config(read_persisted_config()) == validate_persisted_config(candidate)
    assert caplog.records == []
    assert b"\r" not in config_path.read_bytes()
    if "security" in candidate:
        assert "中文项目" in config_path.read_text(encoding="utf-8")
        assert "😀" in config_path.read_text(encoding="utf-8")


def test_invalid_save_does_not_touch_existing_file_or_create_directory(config_path):
    candidate = {"agent": {"proxy": "ftp://user:PRIVATE-PASSWORD@host"}}
    with pytest.raises(ConfigError):
        save_persisted_config(candidate)
    assert not config_path.parent.exists()
    save_persisted_config({"read": {"max_lines": 500}})
    original = config_path.read_bytes()
    with pytest.raises(ConfigError):
        save_persisted_config(candidate)
    assert config_path.read_bytes() == original
    assert list(config_path.parent.iterdir()) == [config_path]


def test_cli_overrides_never_enter_persisted_config_or_update_running_config(config_path):
    save_persisted_config({"read": {"max_lines": 500, "max_bytes": 1024}})
    original = config_path.read_bytes()
    effective = load_config(overrides={"read": {"max_lines": 100, "max_bytes": None}})
    assert effective.read.max_lines == 100 and effective.read.max_bytes == 1024
    assert config_path.read_bytes() == original
    assert read_persisted_config() == {"read": {"max_lines": 500, "max_bytes": 1024}}
    save_persisted_config({"read": {"max_lines": 800}})
    assert effective.read.max_lines == 100 and effective.read.max_bytes == 1024
    assert load_config().read.max_lines == 800
    assert load_config().read.max_bytes == AppConfig().read.max_bytes


def test_atomic_save_flushes_syncs_closes_then_replaces_same_directory(config_path, monkeypatch):
    save_persisted_config({"read": {"max_lines": 500}})
    original = config_path.read_bytes()
    fsync, replace = config_module.os.fsync, config_module.os.replace
    candidate = {"agent": {"device_id": "设备"}}
    synced = []
    def sync(fd):
        assert config_path.read_bytes() == original
        temporary, = config_path.parent.glob(".config-*.tmp")
        # flush has made the entire payload visible while the handle is open.
        assert yaml.safe_load(temporary.read_text(encoding="utf-8")) == candidate
        fsync(fd)
        synced.append(True)
    def commit(source, destination):
        source = Path(source)
        assert synced == [True]
        assert source.parent == config_path.parent and source != config_path
        assert destination == config_path and config_path.read_bytes() == original
        # On Windows, successful replace also proves the writer handle is closed.
        replace(source, destination)
    monkeypatch.setattr(config_module.os, "fsync", sync)
    monkeypatch.setattr(config_module.os, "replace", commit)
    save_persisted_config(candidate)
    assert read_persisted_config() == candidate
    assert list(config_path.parent.iterdir()) == [config_path]


@pytest.mark.parametrize("stage", ["serialize", "open", "write", "flush", "fsync", "replace"])
def test_failed_save_preserves_original_and_cleans_temporary(config_path, monkeypatch, caplog, stage):
    save_persisted_config({"read": {"max_lines": 500}})
    original = config_path.read_bytes()
    candidate = {"agent": {"proxy": "http://user:PRIVATE-CREDENTIAL@host"}}
    def fail(*args, **kwargs):
        kind = yaml.YAMLError if stage == "serialize" else OSError
        raise kind("PRIVATE-PASSWORD http://user:PRIVATE-CREDENTIAL@host")
    if stage == "serialize":
        monkeypatch.setattr(config_module.yaml, "safe_dump", fail)
    elif stage == "open":
        monkeypatch.setattr(config_module.tempfile, "NamedTemporaryFile", fail)
    elif stage in ("write", "flush"):
        temporary_file = config_module.tempfile.NamedTemporaryFile
        @contextmanager
        def broken_file(**kwargs):
            with temporary_file(**kwargs) as handle:
                if stage == "write":
                    write = handle.write
                    def partial_write(content):
                        write(content[:4])
                        fail()
                    handle.write = partial_write
                else:
                    handle.flush = fail
                yield handle
        monkeypatch.setattr(config_module.tempfile, "NamedTemporaryFile", broken_file)
    else:
        monkeypatch.setattr(config_module.os, stage, fail)
    with pytest.raises(ConfigError) as failure:
        save_persisted_config(candidate)
    assert config_path.read_bytes() == original
    assert list(config_path.parent.iterdir()) == [config_path]
    diagnostic = "".join(traceback.format_exception(failure.value))
    assert "PRIVATE-PASSWORD" not in diagnostic and "PRIVATE-CREDENTIAL" not in diagnostic
    assert caplog.records == []


def test_cleanup_failure_does_not_disguise_original_error(config_path, monkeypatch):
    save_persisted_config({"read": {"max_lines": 500}})
    original = config_path.read_bytes()
    def fail_replace(*args):
        raise OSError("PRIVATE-PASSWORD")
    def fail_cleanup(*args, **kwargs):
        raise PermissionError("PRIVATE-CLEANUP")
    monkeypatch.setattr(config_module.os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    with pytest.raises(ConfigError, match="Could not save config file") as failure:
        save_persisted_config({})
    assert config_path.read_bytes() == original
    assert "PRIVATE-CLEANUP" not in str(failure.value)
    # The caller still gets the save error; leftover temp cleanup is best effort.
    assert len(list(config_path.parent.glob(".config-*.tmp"))) == 1


def test_save_normalizes_yaml_and_does_not_preserve_comments(config_path):
    config_path.parent.mkdir()
    config_path.write_text("# hand edited\nagent: {proxy: system} # comment\n", encoding="utf-8")
    save_persisted_config(read_persisted_config())
    assert config_path.read_text(encoding="utf-8") == "agent:\n  proxy: system\n"


def test_explicit_path_supports_read_save_and_home_expansion(tmp_path, config_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    candidate = {"hub": {"token_file": None}}
    save_persisted_config(candidate, "~/settings/config.yaml")
    assert read_persisted_config("~/settings/config.yaml") == candidate
    assert load_config("~/settings/config.yaml").hub.token_file is None
    assert not config_path.exists()
