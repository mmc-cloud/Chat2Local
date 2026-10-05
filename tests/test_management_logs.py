"""Tail cursor behavior under writes, truncation, rotation and partial UTF-8."""

import pytest

from chat2local.management.logs import MAX_LOG_BYTES, MAX_TAIL_LINES, LogReader


def test_missing_created_append_and_partial_utf8(tmp_path):
    path = tmp_path / "chat2local.log"
    reader = LogReader(path)
    assert reader.read()["missing"]
    path.write_bytes("INFO 中文\n".encode())
    first = reader.read()
    assert first["lines"] == ["INFO 中文"]
    assert reader.read(first["cursor"])["lines"] == []
    with path.open("ab") as handle:
        handle.write(b"INFO " + "😀".encode()[:2])
    partial = reader.read(first["cursor"])
    assert (
        partial["lines"] == []
        and partial["cursor"]["offset"] == first["cursor"]["offset"]
    )
    with path.open("ab") as handle:
        handle.write("😀".encode()[2:] + b"\n")
    appended = reader.read(partial["cursor"])
    assert appended["lines"] == ["INFO 😀"] and not appended["reset"]


def test_truncate_rotation_and_missing_then_recreate(tmp_path):
    path = tmp_path / "chat2local.log"
    path.write_bytes(b"INFO old entry\n")
    reader = LogReader(path)
    first = reader.read()
    path.write_bytes(b"INFO new\n")
    truncated = reader.read(first["cursor"])
    assert truncated["reset"] and truncated["lines"] == ["INFO new"]
    path.rename(tmp_path / "chat2local.log.1")
    assert reader.read(truncated["cursor"])["missing"]
    path.write_bytes(b"INFO rotated entry\n")
    rotated = reader.read(truncated["cursor"])
    assert rotated["reset"] and rotated["lines"] == ["INFO rotated entry"]


def test_truncate_and_regrow_is_detected_by_prefix(tmp_path):
    path = tmp_path / "log"
    path.write_bytes(b"INFO old\n")
    reader = LogReader(path)
    first = reader.read()
    path.write_bytes(b"INFO a new larger entry\n")
    second = reader.read(first["cursor"])
    assert second["reset"] and second["lines"] == ["INFO a new larger entry"]


def test_initial_tail_is_bounded(tmp_path):
    path = tmp_path / "log"
    path.write_bytes(b"INFO line\n" * (MAX_TAIL_LINES * 2))
    result = LogReader(path).read()
    assert len(result["lines"]) == MAX_TAIL_LINES
    assert result["cursor"]["offset"] == path.stat().st_size


def test_long_line_makes_progress(tmp_path):
    path = tmp_path / "log"
    path.write_bytes(b"x" * (MAX_LOG_BYTES * 2))
    reader = LogReader(path)
    first = reader.read()
    assert first["cursor"]["offset"] > 0
    assert len(first["lines"][0]) <= MAX_LOG_BYTES


def test_invalid_cursor_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        LogReader(tmp_path / "log").read({"offset": -1})
