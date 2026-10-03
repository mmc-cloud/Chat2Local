"""`search` Tool tests.

Design: docs/design-v0.1.md §4.1 File System → search.

The two backends are covered separately: the Python fallback at the top, then
the ripgrep JSON protocol, then the behaviour both must share.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

import chat2local.tools.search as search_module
from chat2local.runtime.workspace import WorkspaceError, WorkspaceManager
from chat2local.tools.search import search

from conftest import make_file, run


def test_search_name_finds_files(workspace: WorkspaceManager) -> None:
    make_file(workspace, "pkg/module.py", "print('hi')\n")
    make_file(workspace, "pkg/other.txt", "hi\n")

    result = run(search(workspace, "module", mode="name"))

    assert result["result_count"] == 1
    assert result["results"][0]["path"] == "pkg/module.py"
    assert result["results"][0]["type"] == "file"
    assert result["truncated"] is False
    assert result["timed_out"] is False


def test_search_name_ignores_case_by_default(workspace: WorkspaceManager) -> None:
    make_file(workspace, "README.md", "hello\n")

    result = run(search(workspace, "readme", mode="name"))

    assert result["result_count"] == 1


def test_search_name_case_sensitive(workspace: WorkspaceManager) -> None:
    make_file(workspace, "README.md", "hello\n")

    result = run(search(workspace, "readme", mode="name", case_sensitive=True))

    assert result["result_count"] == 0


def test_search_name_uses_regex(workspace: WorkspaceManager) -> None:
    make_file(workspace, "module_a.py", "x\n")
    make_file(workspace, "module_b.py", "x\n")
    make_file(workspace, "notes.md", "x\n")

    result = run(search(workspace, r"module_[ab]\.py", mode="name", regex=True))

    assert {item["path"] for item in result["results"]} == {"module_a.py", "module_b.py"}


def test_search_name_matches_relative_path_when_query_has_separator(workspace: WorkspaceManager) -> None:
    make_file(workspace, "pkg/module.py", "x\n")
    make_file(workspace, "other/module.py", "x\n")

    result = run(search(workspace, "pkg/module", mode="name"))

    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}


def test_search_content_returns_path_line_and_text(workspace: WorkspaceManager) -> None:
    make_file(workspace, "pkg/module.py", "first\nneedle here\nlast\n")

    result = run(search(workspace, "needle", mode="content"))

    assert result["result_count"] == 1
    assert result["results"][0]["path"] == "pkg/module.py"
    assert result["results"][0]["line"] == 2
    assert result["results"][0]["text"] == "needle here"


def test_search_content_uses_regex(workspace: WorkspaceManager) -> None:
    make_file(workspace, "pkg/module.py", "value = 42\nvalue = 7\n")

    result = run(search(workspace, r"value = \d+", mode="content", regex=True))

    assert result["result_count"] == 2
    assert [item["line"] for item in result["results"]] == [1, 2]


def test_search_content_is_case_insensitive_by_default(workspace: WorkspaceManager) -> None:
    make_file(workspace, "pkg/module.py", "Needle\n")

    result = run(search(workspace, "needle", mode="content"))

    assert result["result_count"] == 1


def test_search_content_skips_binary_files(workspace: WorkspaceManager) -> None:
    (workspace.root / "blob.bin").write_bytes(b"needle\x00binary\n")

    result = run(search(workspace, "needle", mode="content"))

    assert result["result_count"] == 0


def test_search_skips_default_excluded_directories(workspace: WorkspaceManager) -> None:
    make_file(workspace, "node_modules/pkg/index.js", "needle\n")
    make_file(workspace, "__pycache__/module.pyc", "needle\n")
    make_file(workspace, ".venv/lib/module.py", "needle\n")
    make_file(workspace, "pkg/module.py", "needle\n")

    result = run(search(workspace, "needle", mode="content"))

    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}


def test_search_skips_git_directory(workspace: WorkspaceManager) -> None:
    make_file(workspace, ".git/config", "needle\n")
    make_file(workspace, "pkg/module.py", "needle\n")

    result = run(search(workspace, "needle", mode="content"))

    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}


def test_search_respects_gitignore(workspace: WorkspaceManager) -> None:
    make_file(workspace, ".gitignore", "ignored.txt\nbuild-output/\n")
    make_file(workspace, "ignored.txt", "needle\n")
    make_file(workspace, "build-output/module.py", "needle\n")
    make_file(workspace, "pkg/module.py", "needle\n")

    result = run(search(workspace, "needle", mode="content"))

    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}


def test_search_searches_hidden_files(workspace: WorkspaceManager) -> None:
    make_file(workspace, ".hidden.py", "needle\n")

    result = run(search(workspace, "needle", mode="content"))

    assert {item["path"] for item in result["results"]} == {".hidden.py"}


def test_search_applies_include_and_exclude_globs(workspace: WorkspaceManager) -> None:
    make_file(workspace, "pkg/module.py", "needle\n")
    make_file(workspace, "pkg/module.txt", "needle\n")

    result = run(search(workspace, "needle", mode="content", include=["*.py"]))

    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}

    result = run(search(workspace, "needle", mode="content", exclude=["*.py"]))

    assert {item["path"] for item in result["results"]} == {"pkg/module.txt"}


def test_search_scoped_to_subdirectory(workspace: WorkspaceManager) -> None:
    make_file(workspace, "pkg/module.py", "needle\n")
    make_file(workspace, "other/module.py", "needle\n")

    result = run(search(workspace, "needle", mode="content", path="pkg"))

    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}


def test_search_truncates_at_max_results(workspace: WorkspaceManager) -> None:
    for index in range(5):
        make_file(workspace, f"pkg/module_{index}.py", "needle\n")

    result = run(search(workspace, "needle", mode="content", max_results=2))

    assert result["result_count"] == 2
    assert result["truncated"] is True


def test_search_without_matches(workspace: WorkspaceManager) -> None:
    make_file(workspace, "pkg/module.py", "hello\n")

    result = run(search(workspace, "needle", mode="content"))

    assert result["results"] == []
    assert result["result_count"] == 0
    assert result["truncated"] is False


def test_search_rejects_invalid_mode(workspace: WorkspaceManager) -> None:
    with pytest.raises(ValueError):
        run(search(workspace, "needle", mode="path"))


def test_search_rejects_zero_max_results(workspace: WorkspaceManager) -> None:
    with pytest.raises(ValueError):
        run(search(workspace, "needle", mode="content", max_results=0))


def test_search_rejects_invalid_regex(workspace: WorkspaceManager) -> None:
    make_file(workspace, "pkg/module.py", "needle\n")

    with pytest.raises(ValueError):
        run(search(workspace, "needle[", mode="content", regex=True))


def test_search_rejects_path_outside_workspace(workspace: WorkspaceManager) -> None:
    with pytest.raises(WorkspaceError):
        run(search(workspace, "needle", mode="content", path="../outside"))


def test_search_missing_path(workspace: WorkspaceManager) -> None:
    with pytest.raises(FileNotFoundError):
        run(search(workspace, "needle", mode="content", path="missing"))


def test_search_reports_python_backend_without_ripgrep(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(search_module.shutil, "which", lambda _: None)

    make_file(workspace, "pkg/module.py", "needle\n")

    result = run(search(workspace, "needle", mode="content"))

    assert result["engine"] == "python"
    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}


# --- Python fallback backend -------------------------------------------------


def force_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_module.shutil, "which", lambda _: None)


def make_outside_symlink(workspace: WorkspaceManager, name: str) -> Path:
    outside = make_secret_outside(workspace)

    link = workspace.root / name

    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks are not available on this system")

    return link


def test_search_fallback_skips_symlink_escaping_workspace(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)
    make_outside_symlink(workspace, "leak.txt")
    make_file(workspace, "pkg/module.py", "needle\n")

    result = run(search(workspace, "needle", mode="content"))

    assert result["engine"] == "python"
    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}


def test_search_fallback_skips_symlink_inside_workspace(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recursive search never follows links, matching ripgrep's default."""

    force_fallback(monkeypatch)

    target = make_file(workspace, "real.txt", "needle\n")
    link = workspace.root / "link.txt"

    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks are not available on this system")

    result = run(search(workspace, "needle", mode="content"))

    assert result["engine"] == "python"
    assert {item["path"] for item in result["results"]} == {"real.txt"}


def test_search_fallback_skips_symlinked_directory(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)

    make_file(workspace, "real/module.py", "needle\n")
    link = workspace.root / "linked"

    try:
        link.symlink_to(workspace.root / "real", target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are not available on this system")

    result = run(search(workspace, "needle", mode="content"))

    assert {item["path"] for item in result["results"]} == {"real/module.py"}


def test_search_fallback_honours_root_gitignore_for_subdirectory_search(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)

    make_file(workspace, ".gitignore", "pkg/ignored.py\n")
    make_file(workspace, "pkg/ignored.py", "needle\n")
    make_file(workspace, "pkg/normal.py", "needle\n")

    result = run(search(workspace, "needle", mode="content", path="pkg"))

    assert result["engine"] == "python"
    assert {item["path"] for item in result["results"]} == {"pkg/normal.py"}


def test_search_fallback_orders_results_deterministically(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)

    for directory in ("zeta", "alpha", "middle"):
        make_file(workspace, f"{directory}/module.py", "needle\n")

    first = run(search(workspace, "needle", mode="content"))["results"]
    second = run(search(workspace, "needle", mode="content"))["results"]

    assert [item["path"] for item in first] == [
        "alpha/module.py",
        "middle/module.py",
        "zeta/module.py",
    ]
    assert first == second


def test_search_fallback_clamps_long_match_lines(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)

    make_file(workspace, "minified.js", "x" * 3000 + "needle" + "y" * 3000 + "\n")

    result = run(search(workspace, "needle", mode="content"))

    item = result["results"][0]
    assert item["text_truncated"] is True
    assert len(item["text"]) <= search_module.DEFAULT_SEARCH_MATCH_TEXT_CHARS
    assert "needle" in item["text"]


def test_search_fallback_stops_at_the_result_payload_budget(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)

    for index in range(60):
        make_file(workspace, f"pkg/file-{index:03}.py", "needle " + "x" * 1500 + "\n")

    result = run(search(workspace, "needle", mode="content", max_results=100))

    assert result["truncated"] is True
    assert result["truncation_reason"] == search_module.OUTPUT_LIMIT
    assert result["timed_out"] is False
    assert 0 < result["result_count"] < 60
    assert search_module._payload_size(result["results"]) <= (
        search_module.DEFAULT_SEARCH_RESULTS_BYTES
    )


def make_secret_outside(workspace: WorkspaceManager) -> Path:
    outside = workspace.root.parent / "secret.txt"
    outside.write_text("needle\n", encoding="utf-8")

    return outside


def force_outside_resolution(monkeypatch: pytest.MonkeyPatch, name: str, target: Path) -> None:
    """Make one entry look like a link that resolves outside the workspace.

    Symlinks need privileges on Windows, so this keeps the containment check covered
    on machines where no symlink can be created.
    """

    real_realpath = os.path.realpath

    monkeypatch.setattr(
        search_module,
        "_is_link_like",
        lambda path: path.name == name,
    )
    monkeypatch.setattr(
        search_module.os.path,
        "realpath",
        lambda path, **kwargs: (
            str(target) if Path(path).name == name else real_realpath(path, **kwargs)
        ),
    )


def test_search_fallback_skips_entries_resolving_outside_workspace(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)

    outside = make_secret_outside(workspace)
    make_file(workspace, "leak.txt", "needle\n")
    make_file(workspace, "pkg/module.py", "needle\n")

    force_outside_resolution(monkeypatch, "leak.txt", outside)

    result = run(search(workspace, "needle", mode="content"))

    assert result["engine"] == "python"
    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}


def test_search_fallback_skips_directory_junction_escaping_workspace(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction test")

    force_fallback(monkeypatch)

    outside_dir = workspace.root.parent / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("needle\n", encoding="utf-8")

    junction = workspace.root / "linked-dir"

    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside_dir)],
        capture_output=True,
        text=True,
    )

    if created.returncode != 0:
        pytest.skip(f"Could not create junction: {created.stderr}")

    make_file(workspace, "pkg/module.py", "needle\n")

    result = run(search(workspace, "needle", mode="content"))

    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}


def test_search_rejects_non_positive_timeout(workspace: WorkspaceManager) -> None:
    with pytest.raises(ValueError):
        run(search(workspace, "needle", mode="content", timeout=0))

    with pytest.raises(ValueError):
        run(search(workspace, "needle", mode="content", timeout=-1.0))


# --- ripgrep backend ---------------------------------------------------------


class FakeStream:
    """Stands in for ``Process.stdout``, which the tool reads in fixed-size chunks."""

    def __init__(self, output: bytes, delay: float = 0.0, chunk: int | None = None) -> None:
        self._data = output
        self._delay = delay
        self._chunk = chunk

    async def read(self, size: int = -1) -> bytes:
        if self._delay:
            await asyncio.sleep(self._delay)

        if self._chunk is not None:
            size = min(size, self._chunk)

        if size < 0 or size >= len(self._data):
            data, self._data = self._data, b""
            return data

        data, self._data = self._data[:size], self._data[size:]
        return data


class FakeProcess:
    """Stands in for the process asyncio hands back from create_subprocess_exec."""

    def __init__(
        self,
        output: list[bytes] | bytes,
        *,
        delay: float = 0.0,
        chunk: int | None = None,
        returncode: int | None = None,
        exit_after_wait: int = 0,
    ) -> None:
        data = output if isinstance(output, bytes) else b"".join(output)

        self.stdout = FakeStream(data, delay, chunk)
        self.returncode = returncode
        self._exit_after_wait = exit_after_wait
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        if not self.killed:
            self.returncode = self._exit_after_wait

        return self.returncode


def match_event(path: str, line: int, text: str) -> bytes:
    event = {
        "type": "match",
        "data": {"path": {"text": path}, "lines": {"text": text}, "line_number": line},
    }

    return (json.dumps(event) + "\n").encode()


def bytes_match_event(path: str, line: int, raw: bytes) -> bytes:
    """A match whose line is not valid UTF-8: ripgrep reports ``lines.bytes``."""

    event = {
        "type": "match",
        "data": {
            "path": {"text": path},
            "lines": {"bytes": base64.b64encode(raw).decode("ascii")},
            "line_number": line,
        },
    }

    return (json.dumps(event) + "\n").encode()


def end_event(path: str, binary_offset: int | None = None) -> bytes:
    """The end event that closes a file; ``binary_offset`` marks it as binary."""

    event = {
        "type": "end",
        "data": {"path": {"text": path}, "binary_offset": binary_offset},
    }

    return (json.dumps(event) + "\n").encode()


def content_events(path: str, *matches: tuple[int, str]) -> list[bytes]:
    """Matches for one file followed by the end event that releases them."""

    return [match_event(path, line, text) for line, text in matches] + [end_event(path)]


def install_fake_ripgrep(
    monkeypatch: pytest.MonkeyPatch, process: FakeProcess
) -> list[list[str]]:
    """Force the ripgrep backend and record the arguments it is called with."""

    calls: list[list[str]] = []

    async def fake_create_subprocess_exec(*args: str, **kwargs: Any) -> FakeProcess:
        calls.append([str(arg) for arg in args])
        return process

    monkeypatch.setattr(search_module.shutil, "which", lambda _: "rg")
    monkeypatch.setattr(search_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    return calls


def test_ripgrep_name_search_lists_files(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess([b"pkg/module.py\n", b"pkg/other.txt\n"])
    calls = install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "module", mode="name"))

    assert result["engine"] == "ripgrep"
    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}

    args = calls[0]
    assert "--files" in args
    assert "--hidden" in args
    assert "--no-require-git" in args
    assert "--path-separator" in args
    assert args[args.index("--sort") + 1] == "path"
    assert "!.git" in args
    assert "!node_modules" in args
    assert args[-1] == "."


def test_ripgrep_content_search_parses_match_events(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess(content_events("./pkg/module.py", (2, "needle here\n")))
    calls = install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "needle", mode="content"))

    assert result["engine"] == "ripgrep"
    assert result["results"] == [{"path": "pkg/module.py", "line": 2, "text": "needle here"}]

    args = calls[0]
    assert "--json" in args
    assert "--fixed-strings" in args
    assert "--ignore-case" in args
    assert args[-2:] == ["needle", "."]


def test_ripgrep_content_search_passes_regex_and_case_flags(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess([])
    calls = install_fake_ripgrep(monkeypatch, process)

    run(search(workspace, "needle\\d", mode="content", regex=True, case_sensitive=True))

    args = calls[0]
    assert "--fixed-strings" not in args
    assert "--case-sensitive" in args
    assert "--ignore-case" not in args


def test_ripgrep_does_not_receive_user_globs(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User globs are applied in one place for both engines, never pushed to ripgrep."""

    process = FakeProcess([])
    calls = install_fake_ripgrep(monkeypatch, process)

    run(search(workspace, "needle", mode="content", include=["*.py"], exclude=["*.min.js"]))

    args = calls[0]
    assert "*.py" not in args
    assert "!*.min.js" not in args

    # the fixed directory excludes are still ripgrep's job
    assert "!node_modules" in args
    assert "!.git" in args


def test_ripgrep_applies_user_globs_to_its_own_matches(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess(
        [
            *content_events("./pkg/keep.py", (1, "needle\n")),
            *content_events("./pkg/drop.min.js", (1, "needle\n")),
        ]
    )
    install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "needle", mode="content", exclude=["*.min.js"]))

    assert {item["path"] for item in result["results"]} == {"pkg/keep.py"}


def test_ripgrep_normal_exit_is_not_reported_as_failure(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    # asyncio can still report returncode None at the moment stdout hits EOF.
    process = FakeProcess(content_events("./pkg/module.py", (3, "needle\n")))
    install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "needle", mode="content"))

    assert result["results"] == [{"path": "pkg/module.py", "line": 3, "text": "needle"}]
    assert process.killed is False


def test_ripgrep_result_cap_is_not_reported_as_failure(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess(
        [
            *content_events("./a.py", (1, "needle\n")),
            *content_events("./b.py", (1, "needle\n")),
        ]
    )
    install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "needle", mode="content", max_results=1))

    assert result["result_count"] == 1
    assert result["truncated"] is True
    assert result["truncation_reason"] == search_module.MAX_RESULTS
    assert result["timed_out"] is False
    assert process.killed is True


def test_ripgrep_timeout_is_not_reported_as_failure(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess([], delay=5.0)
    install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "needle", mode="content", timeout=0.01))

    assert result["timed_out"] is True
    assert result["results"] == []
    assert result["truncated"] is True
    assert result["truncation_reason"] == search_module.TIMEOUT
    assert process.killed is True


def test_ripgrep_failure_is_raised(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess([], returncode=2, exit_after_wait=2)
    install_fake_ripgrep(monkeypatch, process)

    with pytest.raises(RuntimeError):
        run(search(workspace, "needle", mode="content"))


def test_ripgrep_long_match_line_is_clamped(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 200 KB in one line: readline() would raise ValueError on the buffer limit
    process = FakeProcess(
        content_events("./big.js", (1, "x" * 3000 + "needle" + "y" * 200_000 + "\n"))
    )
    install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "needle", mode="content"))

    item = result["results"][0]
    assert item["text_truncated"] is True
    assert len(item["text"]) <= search_module.DEFAULT_SEARCH_MATCH_TEXT_CHARS
    assert "needle" in item["text"]


def test_ripgrep_reassembles_lines_across_chunks(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess(
        [b"pkg/other.txt\n", *content_events("./pkg/module.py", (5, "needle\n"))], chunk=7
    )
    install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "needle", mode="content"))

    assert result["results"] == [{"path": "pkg/module.py", "line": 5, "text": "needle"}]


def test_ripgrep_stops_at_the_result_text_budget(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [
        event
        for index in range(60)
        for event in content_events(f"./pkg/file-{index}.py", (1, "needle" + "x" * 1500 + "\n"))
    ]
    install_fake_ripgrep(monkeypatch, FakeProcess(events))

    result = run(search(workspace, "needle", mode="content", max_results=100))

    assert result["truncated"] is True
    assert result["truncation_reason"] == search_module.OUTPUT_LIMIT
    assert result["timed_out"] is False
    assert 0 < result["result_count"] < 60
    assert search_module._payload_size(result["results"]) <= (
        search_module.DEFAULT_SEARCH_RESULTS_BYTES
    )


def test_ripgrep_name_search_does_not_follow_symlinks(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ripgrep lists links, which is safe because the file Tools refuse them."""

    outside = make_secret_outside(workspace)
    link = workspace.root / "leak.txt"

    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks are not available on this system")

    from chat2local.tools.read import read

    with pytest.raises(WorkspaceError):
        run(read(workspace, "leak.txt"))

# --- truncation protocol -------------------------------------------------------


def test_truncation_reason_is_none_without_truncation(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)
    make_file(workspace, "pkg/module.py", "needle\n")

    result = run(search(workspace, "needle", mode="content"))

    assert result["truncated"] is False
    assert result["truncation_reason"] is None
    assert result["timed_out"] is False


def test_truncation_reason_max_results(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)

    for index in range(5):
        make_file(workspace, f"pkg/module_{index}.py", "needle\n")

    result = run(search(workspace, "needle", mode="content", max_results=2))

    assert result["truncated"] is True
    assert result["truncation_reason"] == search_module.MAX_RESULTS
    assert result["timed_out"] is False


def test_truncation_reason_timeout(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)

    for index in range(400):
        make_file(workspace, f"pkg/file-{index:03}.py", "needle " + "x" * 500 + "\n")

    result = run(search(workspace, "needle", mode="content", timeout=0.0001, max_results=500))

    assert result["truncated"] is True
    assert result["truncation_reason"] == search_module.TIMEOUT
    assert result["timed_out"] is True


def test_truncation_reason_output_limit(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)

    for index in range(60):
        make_file(workspace, f"pkg/file-{index:03}.py", "needle " + "x" * 1500 + "\n")

    result = run(search(workspace, "needle", mode="content", max_results=500))

    assert result["truncated"] is True
    assert result["truncation_reason"] == search_module.OUTPUT_LIMIT
    assert result["timed_out"] is False


# --- match-centred snippets ----------------------------------------------------


def test_search_fallback_snippet_centres_a_late_match(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)

    line = "x" * 3000 + "needle" + "y" * 3000
    make_file(workspace, "minified.js", line + "\n")

    result = run(search(workspace, "needle", mode="content"))

    item = result["results"][0]
    snippet = item["text"]

    assert item["text_truncated"] is True
    assert len(snippet) <= search_module.DEFAULT_SEARCH_MATCH_TEXT_CHARS
    assert "needle" in snippet
    assert snippet in line
    # the match lands in the middle of the returned window
    assert snippet.index("needle") == (len(snippet) - len("needle")) // 2


def test_search_snippet_keeps_a_match_wider_than_the_window(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)

    make_file(workspace, "wide.txt", "a" * 10 + "n" * 3000 + "b" * 10 + "\n")

    result = run(search(workspace, r"n{1000}", mode="content", regex=True))

    item = result["results"][0]
    assert item["text_truncated"] is True
    assert len(item["text"]) == search_module.DEFAULT_SEARCH_MATCH_TEXT_CHARS
    # a match wider than the window is handed back from its own start
    assert item["text"].count("a") == 10
    assert set(item["text"]) == {"a", "n"}


# --- binary and encoding handling ----------------------------------------------


def test_ripgrep_drops_matches_from_a_file_reported_binary(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A binary file can emit a match before its end event says it is binary."""

    make_file(workspace, "pkg/module.py", "needle\n")

    process = FakeProcess(
        [
            match_event("./blob.bin", 1, "needle\n"),
            end_event("./blob.bin", binary_offset=6),
            *content_events("./pkg/module.py", (1, "needle\n")),
        ]
    )
    install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "needle", mode="content"))

    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}


def test_ripgrep_skips_matches_whose_lines_are_bytes(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-UTF-8 lines arrive as ``lines.bytes`` and cannot be matched as text."""

    make_file(workspace, "pkg/module.py", "needle\n")

    process = FakeProcess(
        [
            bytes_match_event("./blob.bin", 1, b"needle\x00binary\n"),
            end_event("./blob.bin"),
            *content_events("./pkg/module.py", (1, "needle\n")),
        ]
    )
    install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "needle", mode="content"))

    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}


def test_ripgrep_holds_matches_until_the_file_end_event(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A match with no end event is never released, so nothing is committed."""

    process = FakeProcess([match_event("./pkg/module.py", 1, "needle\n")])
    install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "needle", mode="content"))

    assert result["results"] == []
    assert result["truncated"] is False


def test_search_fallback_skips_invalid_utf8(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)

    (workspace.root / "broken.txt").write_bytes(b"needle \xff\xfe here\n")
    make_file(workspace, "pkg/module.py", "needle\n")

    result = run(search(workspace, "needle", mode="content"))

    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}


# --- one glob semantics for both engines ---------------------------------------


GLOB_FILES = ("pkg/module.py", "pkg/module.txt", "pkg/deep/other.py", "other/module.py")


def glob_workspace(workspace: WorkspaceManager) -> None:
    for path in GLOB_FILES:
        make_file(workspace, path, "needle\n")


def glob_events() -> list[bytes]:
    """Every file ``glob_workspace`` creates, as ripgrep would report them."""

    return [
        event
        for path in GLOB_FILES
        for event in content_events("./" + path, (1, "needle\n"))
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"include": ["*.py"]},
        {"include": ["pkg/*"]},
        {"include": ["pkg/*.py"]},
        {"include": ["*/deep/*"]},
        {"include": ["pkg/**"]},
        {"exclude": ["*.py"]},
        {"exclude": ["pkg"]},
        {"exclude": ["pkg/**"]},
        {"exclude": ["deep"]},
        {"include": ["*.py"], "exclude": ["*module.txt"]},
        {"include": ["other", "pkg/deep"]},
    ],
)
def test_both_engines_apply_the_same_globs(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any]
) -> None:
    glob_workspace(workspace)

    force_fallback(monkeypatch)
    expected = {
        item["path"]
        for item in run(search(workspace, "needle", mode="content", **kwargs))["results"]
    }

    install_fake_ripgrep(monkeypatch, FakeProcess(glob_events()))
    actual = {
        item["path"]
        for item in run(search(workspace, "needle", mode="content", **kwargs))["results"]
    }

    assert actual == expected


# --- fallback timeout propagation ----------------------------------------------


def test_fallback_reports_timeout_inside_the_last_file(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deadline reached inside the only file must still surface as a timeout.

    Nothing follows that file, so no later directory or candidate would trip an
    outer deadline check: the signal has to come from the read loop itself.
    """

    force_fallback(monkeypatch)
    make_file(workspace, "only.txt", "needle\n" * 400_000)

    result = run(
        search(workspace, "needle", mode="content", path="only.txt", timeout=0.05, max_results=500)
    )

    assert result["truncated"] is True
    assert result["truncation_reason"] == search_module.TIMEOUT
    assert result["timed_out"] is True


def test_fallback_reports_timeout_while_walking_directories(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_fallback(monkeypatch)

    for index in range(400):
        make_file(workspace, f"pkg/file-{index:03}.py", "needle " + "x" * 500 + "\n")

    result = run(search(workspace, "needle", mode="content", timeout=0.0001, max_results=500))

    assert result["truncated"] is True
    assert result["truncation_reason"] == search_module.TIMEOUT
    assert result["timed_out"] is True


def test_fallback_timeout_beats_a_full_result_set(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reaching the deadline is a timeout even when the file also filled the caps."""

    force_fallback(monkeypatch)
    make_file(workspace, "only.txt", "needle\n" * 400)

    calls = {"n": 0}
    real_check = search_module._check_deadline

    def expire_after_first_check(deadline: float) -> None:
        calls["n"] += 1

        if calls["n"] > 1:
            raise search_module._SearchTimedOut

        real_check(deadline)

    monkeypatch.setattr(search_module, "_check_deadline", expire_after_first_check)

    result = run(search(workspace, "needle", mode="content", path="only.txt"))

    assert result["truncation_reason"] == search_module.TIMEOUT
    assert result["timed_out"] is True


# --- ripgrep pending buffer is capped ------------------------------------------


def test_ripgrep_pending_stops_growing_past_max_results(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One huge file must not buffer matches the search can never return."""

    make_file(workspace, "only.txt", "needle\n")

    emitted = 5000
    seen: list[int] = []
    real_cap = search_module._cap_reason

    def counting_cap(results, item, limit):
        seen.append(len(results))
        return real_cap(results, item, limit)

    monkeypatch.setattr(search_module, "_cap_reason", counting_cap)

    process = FakeProcess(
        [
            *[match_event("./only.txt", index, "needle\n") for index in range(1, emitted + 1)],
            end_event("./only.txt"),
        ]
    )
    install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "needle", mode="content", max_results=100))

    assert result["result_count"] == 100
    assert result["truncation_reason"] == search_module.MAX_RESULTS

    # The buffer is vetted per match, so checking stops at the cap instead of
    # running to the end of the file. Without that, one call per emitted match
    # would reach `emitted` and every one of them would be buffered.
    assert max(seen) <= 100
    assert len(seen) < emitted


def test_ripgrep_pending_stops_growing_past_the_payload_budget(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_file(workspace, "only.txt", "needle\n")

    emitted = 400
    process = FakeProcess(
        [
            *[
                match_event("./only.txt", index, "needle " + "x" * 1500 + "\n")
                for index in range(1, emitted + 1)
            ],
            end_event("./only.txt"),
        ]
    )
    install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "needle", mode="content", max_results=500))

    assert result["truncation_reason"] == search_module.OUTPUT_LIMIT
    assert 0 < result["result_count"] < emitted
    assert search_module._payload_size(result["results"]) <= (
        search_module.DEFAULT_SEARCH_RESULTS_BYTES
    )


def test_ripgrep_binary_file_never_caps_the_search(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Matches buffered for a file later proven binary must not truncate anything."""

    make_file(workspace, "pkg/module.py", "needle\n")

    process = FakeProcess(
        [
            *[match_event("./blob.bin", index, "needle\n") for index in range(1, 5001)],
            end_event("./blob.bin", binary_offset=6),
            *content_events("./pkg/module.py", (1, "needle\n")),
        ]
    )
    install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "needle", mode="content", max_results=100))

    assert {item["path"] for item in result["results"]} == {"pkg/module.py"}
    assert result["truncated"] is False
    assert result["truncation_reason"] is None


# --- fixed excludes are not addressable through path= --------------------------


def fixed_excluded_workspace(workspace: WorkspaceManager) -> None:
    make_file(workspace, ".git/config", "needle\n")
    make_file(workspace, "node_modules/pkg/index.js", "needle\n")
    make_file(workspace, "__pycache__/module.pyc", "needle\n")
    make_file(workspace, "pkg/module.py", "needle\n")


@pytest.mark.parametrize(
    "path",
    [".git", ".git/", "node_modules", "node_modules/pkg", "__pycache__", "pkg/../.git"],
)
def test_search_path_inside_a_fixed_excluded_directory_is_empty(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    force_fallback(monkeypatch)
    fixed_excluded_workspace(workspace)

    result = run(search(workspace, "needle", mode="content", path=path))

    assert result["results"] == []
    assert result["result_count"] == 0
    assert result["truncated"] is False
    assert result["truncation_reason"] is None
    assert result["timed_out"] is False


def test_search_path_inside_a_fixed_excluded_directory_is_empty_for_ripgrep(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_excluded_workspace(workspace)

    process = FakeProcess([match_event("./.git/config", 1, "needle\n"), end_event("./.git/config")])
    install_fake_ripgrep(monkeypatch, process)

    result = run(search(workspace, "needle", mode="content", path=".git"))

    assert result["engine"] == "ripgrep"
    assert result["results"] == []
    assert result["truncation_reason"] is None


@pytest.mark.parametrize("path", [".", "pkg", "pkg/module.py"])
def test_search_path_outside_the_fixed_excludes_still_works(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    force_fallback(monkeypatch)
    fixed_excluded_workspace(workspace)

    result = run(search(workspace, "needle", mode="content", path=path))

    assert [item["path"] for item in result["results"]] == ["pkg/module.py"]


def test_workspace_root_is_not_treated_as_excluded(workspace: WorkspaceManager) -> None:
    """``.`` must never be read as a fixed-exclude hit."""

    assert search_module._is_fixed_excluded_path(".") is False
    assert search_module._is_fixed_excluded_path("pkg/module.py") is False
    assert search_module._is_fixed_excluded_path(".git") is True
    assert search_module._is_fixed_excluded_path("a/node_modules/b") is True
