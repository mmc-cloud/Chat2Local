"""Native lock behavior, portable branch boundaries and descriptor ownership."""

from dataclasses import asdict, replace
from datetime import datetime
import errno
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
from types import SimpleNamespace
from uuid import UUID

import pytest

from chat2local.runtime.instance import AlreadyRunningError, InstanceLock, RuntimeDescriptor


def python_script(script):
    # Windows venv python.exe is a launcher; use the interpreter directly so the
    # Popen PID is the Core PID and kill tests exercise the actual lock holder.
    executable = sys._base_executable if sys.platform == "win32" else sys.executable
    source = str(Path(__file__).resolve().parents[1] / "src")
    bootstrap = (f"import sys, site; site.addsitedir({sysconfig.get_path('purelib')!r}); "
                 f"sys.path.insert(0, {source!r})\n")
    return [executable, "-u", "-c", bootstrap + script]


def test_native_lock_release_and_residual_file(tmp_path):
    first, second = InstanceLock(tmp_path), InstanceLock(tmp_path)
    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError, match="already running"):
            second.acquire()
        assert second._handle is None
    finally:
        first.release()
    assert first.path.exists()
    second.acquire()
    second.release()
    second.release()


def test_lock_is_exclusive_across_processes_and_released_on_kill(tmp_path):
    script = """
import sys
from pathlib import Path
from chat2local.runtime.instance import InstanceLock
lock = InstanceLock(Path(sys.argv[1]))
lock.acquire()
print('locked', flush=True)
sys.stdin.read()
"""
    child = subprocess.Popen([*python_script(script), str(tmp_path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}))
    contender = InstanceLock(tmp_path)
    try:
        assert child.stdout.readline().strip() == b"locked"
        with pytest.raises(AlreadyRunningError):
            contender.acquire()
        child.kill()
        child.wait(timeout=10)
        contender.acquire()
    finally:
        contender.release()
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_os_lock_api_boundary(tmp_path, monkeypatch, platform):
    calls = []
    if platform == "win32":
        module = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2,
                                 locking=lambda fd, flag, length: calls.append((fd, flag, length)))
        monkeypatch.setitem(sys.modules, "msvcrt", module)
    else:
        module = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4,
                                 flock=lambda fd, flag: calls.append((fd, flag)))
        monkeypatch.setitem(sys.modules, "fcntl", module)
    lock = InstanceLock(tmp_path, platform=platform)
    lock.acquire()
    fd = lock._handle.fileno()
    lock.release()
    assert calls == ([(fd, 1, 1), (fd, 2, 1)] if platform == "win32" else [(fd, 3), (fd, 4)])
    assert lock._handle is None


@pytest.mark.parametrize("code,kind", [(errno.EACCES, AlreadyRunningError), (errno.EAGAIN, AlreadyRunningError),
                                      (errno.EIO, OSError)])
def test_lock_failures_close_handle(tmp_path, monkeypatch, code, kind):
    lock = InstanceLock(tmp_path)
    def fail(handle, *, unlock):
        raise OSError(code, "test failure")
    monkeypatch.setattr(lock, "_lock", fail)
    with pytest.raises(kind):
        lock.acquire()
    assert lock._handle is None
    lock.path.unlink()  # no leaked handle on Windows


@pytest.mark.parametrize("mode", ["agent", "hub", "standalone"])
def test_descriptor_schema_and_no_secrets(tmp_path, mode):
    descriptor = RuntimeDescriptor.new(mode, "my-device", tmp_path.resolve(), 12345)
    data = asdict(descriptor)
    assert set(data) == {"schema_version", "instance_id", "pid", "mode", "device_id", "workspace",
                         "started_at", "version", "control"}
    assert data["schema_version"] == 1 and data["mode"] == mode
    assert data["pid"] == os.getpid() and data["workspace"] == str(tmp_path.resolve())
    assert data["device_id"] == "my-device" and data["version"] == version("chat2local")
    assert UUID(data["instance_id"]).version == 4
    assert data["instance_id"] != RuntimeDescriptor.new(mode, "my-device", tmp_path, 12345).instance_id
    assert data["started_at"].endswith("Z") and datetime.fromisoformat(data["started_at"]).utcoffset().total_seconds() == 0
    assert data["control"] == {"transport": "tcp", "host": "127.0.0.1", "port": 12345}


def test_atomic_publish_replaces_stale_and_owned_cleanup(tmp_path, monkeypatch):
    path = tmp_path / "runtime.json"
    path.write_text('{"instance_id":"stale"}', encoding="utf-8")
    descriptor = RuntimeDescriptor.new("hub", "device", tmp_path, 12345)
    real_replace = os.replace
    def checked_replace(source, target):
        assert path.read_text() == '{"instance_id":"stale"}'
        assert json.loads(Path(source).read_text(encoding="utf-8")) == asdict(descriptor)
        assert Path(source).parent == tmp_path
        real_replace(source, target)
    monkeypatch.setattr(os, "replace", checked_replace)
    descriptor.publish(tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == asdict(descriptor)
    descriptor.remove(tmp_path)
    assert not path.exists() and list(tmp_path.glob(".runtime-*")) == []


def test_descriptor_does_not_remove_other_instance_or_invalid_data(tmp_path):
    descriptor = RuntimeDescriptor.new("agent", "device", tmp_path, 12345)
    other = replace(descriptor, instance_id="different-instance")
    other.publish(tmp_path)
    descriptor.remove(tmp_path)
    assert json.loads((tmp_path / "runtime.json").read_text())["instance_id"] == other.instance_id
    for raw in ("invalid", "[]", "{}"):
        (tmp_path / "runtime.json").write_text(raw)
        descriptor.remove(tmp_path)
        assert (tmp_path / "runtime.json").read_text() == raw


def test_failed_atomic_publish_preserves_old_descriptor(tmp_path, monkeypatch):
    path = tmp_path / "runtime.json"
    path.write_text("old")
    descriptor = RuntimeDescriptor.new("hub", "device", tmp_path, 12345)
    def fail(*args):
        raise OSError("publish failed")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        descriptor.publish(tmp_path)
    assert path.read_text() == "old" and list(tmp_path.glob(".runtime-*")) == []
