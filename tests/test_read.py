"""`read` Tool tests.

Design: docs/design-v0.1.md §4.1 File System → read.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import chat2local.tools.read as read_module
from chat2local.runtime.workspace import WorkspaceError, WorkspaceManager
from chat2local.tools.read import read

from conftest import make_file, run

LINE = "line {index}\n"


def test_read_returns_file_text(workspace: WorkspaceManager) -> None:
    make_file(workspace, "notes.txt", "alpha\nbeta\n")

    result = run(read(workspace, "notes.txt"))

    assert result["kind"] == "file"
    assert result["path"] == "notes.txt"
    assert result["text"] == "alpha\nbeta\n"
    assert result["lines_returned"] == 2
    assert result["start_line"] == 1
    assert result["end_line"] == 2
    assert result["total_lines"] == 2
    assert result["truncated"] is False
    assert result["truncation_reason"] is None
    assert result["next_start_line"] is None


def test_read_line_range(workspace: WorkspaceManager) -> None:
    make_file(workspace, "notes.txt", "".join(LINE.format(index=i) for i in range(1, 11)))

    result = run(read(workspace, "notes.txt", start_line=3, end_line=5))

    assert result["text"] == "line 3\nline 4\nline 5\n"
    assert result["start_line"] == 3
    assert result["end_line"] == 5
    assert result["lines_returned"] == 3
    assert result["truncated"] is False
    assert result["truncation_reason"] is None
    assert result["total_lines"] is None


def test_read_truncates_at_max_lines(workspace: WorkspaceManager) -> None:
    make_file(workspace, "notes.txt", "".join(LINE.format(index=i) for i in range(1, 6)))

    result = run(read(workspace, "notes.txt", max_lines=2))

    assert result["text"] == "line 1\nline 2\n"
    assert result["lines_returned"] == 2
    assert result["truncated"] is True
    assert result["truncation_reason"] == "line_limit"
    assert result["next_start_line"] == 3
    assert result["total_lines"] is None


def test_read_truncates_at_max_bytes(workspace: WorkspaceManager) -> None:
    make_file(workspace, "notes.txt", "".join(LINE.format(index=i) for i in range(1, 6)))

    result = run(read(workspace, "notes.txt", max_bytes=14))

    assert result["text"] == "line 1\nline 2\n"
    assert result["truncated"] is True
    assert result["truncation_reason"] == "byte_limit"
    assert result["next_start_line"] == 3


def test_read_cuts_single_line_larger_than_budget(workspace: WorkspaceManager) -> None:
    make_file(workspace, "minified.js", "x" * 500 + "\n")

    result = run(read(workspace, "minified.js", max_bytes=100))

    assert result["lines_returned"] == 1
    assert result["truncated"] is True
    assert result["truncation_reason"] == "line_too_long"
    assert result["line_truncated"] is True
    assert result["next_start_line"] == 2
    assert len(result["text"]) == 100


def test_read_returns_utf8_prefix_of_an_overlong_line(workspace: WorkspaceManager) -> None:
    make_file(workspace, "wide.txt", "中" * 10 + "\n")

    result = run(read(workspace, "wide.txt", max_bytes=10))

    assert result["truncation_reason"] == "line_too_long"
    assert result["line_truncated"] is True
    assert result["text"] == "中中中"
    assert result["next_start_line"] == 2


def test_read_marks_line_too_long_after_complete_lines(workspace: WorkspaceManager) -> None:
    make_file(workspace, "mixed.txt", "short\n" + "x" * 100 + "\n")

    result = run(read(workspace, "mixed.txt", max_bytes=20))

    assert result["text"] == "short\n" + "x" * 14
    assert result["start_line"] == 1
    assert result["end_line"] == 2
    assert result["truncation_reason"] == "line_too_long"
    assert result["line_truncated"] is True
    assert result["next_start_line"] == 3


def test_read_does_not_consume_a_line_when_no_byte_budget_remains(
    workspace: WorkspaceManager,
) -> None:
    make_file(workspace, "notes.txt", "12345\n" + "x" * 20 + "\n")

    result = run(read(workspace, "notes.txt", max_bytes=6))

    assert result["text"] == "12345\n"
    assert result["lines_returned"] == 1
    assert result["end_line"] == 1
    assert result["truncated"] is True
    assert result["truncation_reason"] == "byte_limit"
    assert result["line_truncated"] is False
    assert result["next_start_line"] == 2


def test_read_does_not_mark_a_line_returned_when_only_a_partial_character_fits(
    workspace: WorkspaceManager,
) -> None:
    make_file(workspace, "notes.txt", "ab\n" + "中" * 4 + "\n")

    result = run(read(workspace, "notes.txt", max_bytes=5))

    assert result["text"] == "ab\n"
    assert result["lines_returned"] == 1
    assert result["end_line"] == 1
    assert result["truncation_reason"] == "byte_limit"
    assert result["line_truncated"] is False
    assert result["next_start_line"] == 2


def test_read_rejects_invalid_utf8_instead_of_replacement_characters(
    workspace: WorkspaceManager,
) -> None:
    (workspace.root / "broken.txt").write_bytes(b"alpha\n\xff\xfe\n")

    with pytest.raises(ValueError, match="unsupported or invalid text encoding"):
        run(read(workspace, "broken.txt"))


def test_read_rejects_truncated_utf16(workspace: WorkspaceManager) -> None:
    (workspace.root / "wide.txt").write_bytes(b"\xff\xfe\x41")

    with pytest.raises(ValueError, match="unsupported or invalid text encoding"):
        run(read(workspace, "wide.txt"))


def test_read_binary_file_is_not_returned(workspace: WorkspaceManager) -> None:
    target = workspace.root / "blob.bin"
    target.write_bytes(b"\x00\x01\x02binary")

    result = run(read(workspace, "blob.bin"))

    assert result["kind"] == "binary"
    assert "text" not in result
    assert result["size"] == 9
    assert result["truncation_reason"] is None


def test_read_utf16_file_with_bom(workspace: WorkspaceManager) -> None:
    make_file(workspace, "wide.txt", "中文\n第二行\n", encoding="utf-16")

    result = run(read(workspace, "wide.txt"))

    assert result["encoding"] == "utf-16"
    assert result["text"] == "中文\n第二行\n"


def test_read_utf8_file_with_bom(workspace: WorkspaceManager) -> None:
    make_file(workspace, "bom.txt", "hello\n", encoding="utf-8-sig")

    result = run(read(workspace, "bom.txt"))

    assert result["encoding"] == "utf-8-sig"
    assert result["text"] == "hello\n"


def test_read_directory_lists_one_level_only(workspace: WorkspaceManager) -> None:
    make_file(workspace, "pkg/module.py", "print('hi')\n")
    make_file(workspace, "pkg/nested/deep.py", "print('deep')\n")

    result = run(read(workspace, "pkg"))

    assert result["kind"] == "directory"
    assert result["path"] == "pkg"
    assert result["entry_count"] == 2
    assert result["truncated"] is False
    assert result["truncation_reason"] is None
    assert [entry["name"] for entry in result["entries"]] == ["module.py", "nested"]
    assert [entry["type"] for entry in result["entries"]] == ["file", "dir"]
    assert result["entries"][0]["size"] == 12


def test_read_directory_honours_caller_max_bytes(workspace: WorkspaceManager) -> None:
    for index in range(20):
        make_file(workspace, f"pkg/file-{index:02}.txt", "x\n")

    result = run(read(workspace, "pkg", max_bytes=10))

    assert result["kind"] == "directory"
    assert result["truncated"] is True
    assert result["truncation_reason"] == "byte_limit"
    assert result["entry_count"] < 20


def test_read_directory_budget_uses_the_entries_payload_size(
    workspace: WorkspaceManager,
) -> None:
    make_file(workspace, "pkg/a.txt", "x\n")
    make_file(workspace, "pkg/b.txt", "x\n")

    entries = [
        {"name": "a.txt", "type": "file", "size": 2},
        {"name": "b.txt", "type": "file", "size": 2},
    ]
    exact = len(json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    fits = run(read(workspace, "pkg", max_bytes=exact))

    assert fits["entry_count"] == 2
    assert fits["truncated"] is False
    assert fits["truncation_reason"] is None

    tight = run(read(workspace, "pkg", max_bytes=exact - 1))

    assert tight["entry_count"] == 1
    assert tight["truncated"] is True
    assert tight["truncation_reason"] == "byte_limit"


def test_read_directory_ignores_envelope_metadata_in_the_budget(
    workspace: WorkspaceManager,
) -> None:
    make_file(workspace, "pkg/a.txt", "x\n")

    entries = [{"name": "a.txt", "type": "file", "size": 2}]
    exact = len(json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    result = run(read(workspace, "pkg", max_bytes=exact))

    assert result["entry_count"] == 1
    assert result["truncated"] is False

    # The serialized result is larger than the budget, which only bounds the entries payload.
    whole = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    assert len(whole) > exact


def test_read_directory_reports_windows_junctions_as_links(workspace: WorkspaceManager) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction test")

    make_file(workspace, "pkg/nested/deep.py", "x\n")

    junction = workspace.root / "pkg" / "link"

    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(workspace.root / "pkg" / "nested")],
        capture_output=True,
        text=True,
    )

    if created.returncode != 0:
        pytest.skip(f"Could not create junction: {created.stderr}")

    result = run(read(workspace, "pkg"))

    kinds = {entry["name"]: entry["type"] for entry in result["entries"]}

    assert kinds["link"] == "symlink"
    assert kinds["nested"] == "dir"
    assert result["entry_count"] == 2


def test_read_directory_reports_scan_limit(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(5):
        make_file(workspace, f"pkg/file-{index}.txt", "x\n")

    monkeypatch.setattr(read_module, "MAX_DIRECTORY_SCAN_ENTRIES", 3)

    result = run(read(workspace, "pkg", max_bytes=64 * 1024))

    assert result["entry_count"] == 3
    assert result["truncated"] is True
    assert result["truncation_reason"] == "scan_limit"


def test_read_directory_reports_byte_limit_when_the_scan_stopped_early_too(
    workspace: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(5):
        make_file(workspace, f"pkg/file-{index}.txt", "x\n")

    monkeypatch.setattr(read_module, "MAX_DIRECTORY_SCAN_ENTRIES", 3)

    result = run(read(workspace, "pkg", max_bytes=10))

    assert result["truncated"] is True
    assert result["truncation_reason"] == "byte_limit"


def test_read_rejects_non_positive_budgets(workspace: WorkspaceManager) -> None:
    make_file(workspace, "notes.txt", "alpha\n")

    with pytest.raises(ValueError):
        run(read(workspace, "notes.txt", max_lines=0))

    with pytest.raises(ValueError):
        run(read(workspace, "notes.txt", max_lines=-1))

    with pytest.raises(ValueError):
        run(read(workspace, "notes.txt", max_bytes=0))

    with pytest.raises(ValueError):
        run(read(workspace, "notes.txt", max_bytes=-1))


def test_read_start_line_beyond_end_of_file(workspace: WorkspaceManager) -> None:
    make_file(workspace, "notes.txt", "only\n")

    result = run(read(workspace, "notes.txt", start_line=50))

    assert result["lines_returned"] == 0
    assert result["text"] == ""
    assert result["start_line"] is None
    assert result["total_lines"] == 1
    assert result["truncated"] is False
    assert result["truncation_reason"] is None


def test_read_missing_path(workspace: WorkspaceManager) -> None:
    with pytest.raises(FileNotFoundError):
        run(read(workspace, "missing.txt"))


def test_read_rejects_path_outside_workspace(workspace: WorkspaceManager) -> None:
    with pytest.raises(WorkspaceError):
        run(read(workspace, "../outside.txt"))


def test_read_rejects_invalid_range(workspace: WorkspaceManager) -> None:
    make_file(workspace, "notes.txt", "alpha\n")

    with pytest.raises(ValueError):
        run(read(workspace, "notes.txt", start_line=5, end_line=2))


def test_read_rejects_zero_start_line(workspace: WorkspaceManager) -> None:
    make_file(workspace, "notes.txt", "alpha\n")

    with pytest.raises(ValueError):
        run(read(workspace, "notes.txt", start_line=0))
