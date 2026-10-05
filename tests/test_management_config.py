"""Sparse config and restart notice via the framework-independent adapter."""

import pytest
from conftest import run

from chat2local.management.adapter import CoreAdapter
from chat2local.management.runtime import RuntimeClient
from chat2local.runtime import config


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    return CoreAdapter(RuntimeClient(tmp_path))


def test_persisted_effective_and_sparse_semantics(adapter):
    assert adapter.read_config()["persisted"] == {}
    assert adapter.read_config()["effective"]["read"]["max_lines"] == 1000
    candidate = {"read": {"max_lines": 1000}, "agent": {"token_file": None}}
    run(adapter.save_config(candidate))
    assert adapter.read_config()["persisted"] == candidate
    assert "max_bytes" not in adapter.read_config()["persisted"]["read"]
    run(adapter.save_config({"agent": {"token_file": None}}))
    assert "read" not in adapter.read_config()["persisted"]
    assert adapter.read_config()["effective"]["read"]["max_lines"] == 1000


def test_invalid_save_does_not_write(adapter):
    run(adapter.save_config({"read": {"max_lines": 5}}))
    before = config.DEFAULT_CONFIG_PATH.read_bytes()
    with pytest.raises(config.ConfigError):
        run(adapter.save_config({"read": {"max_lines": 0}}))
    assert config.DEFAULT_CONFIG_PATH.read_bytes() == before


def test_restart_notice_tracks_running_instance(adapter, monkeypatch):
    core = {"instance_id": "one"}

    async def status():
        return {"lifecycle": "Running", "core": core.copy()}

    monkeypatch.setattr(adapter.runtime, "discover", status)
    monkeypatch.setattr(adapter.runtime, "status", status)
    assert run(adapter.save_config({}))["restart_required"]
    assert run(adapter.runtime_status())["restart_required"]
    core["instance_id"] = "two"
    assert not run(adapter.runtime_status())["restart_required"]


def test_save_stopped_does_not_require_restart(adapter):
    assert not run(adapter.save_config({}))["restart_required"]
