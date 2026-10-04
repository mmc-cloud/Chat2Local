"""Readable workspace identities inside Chat2Local's internal user data."""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from chat2local.handoff.models import InvalidHandoffError

MAX_WORKSPACE_KEY_BYTES = 200


def _short_hash(canonical_path: str) -> str:
    return hashlib.sha256(os.path.normcase(canonical_path).encode("utf-8")).hexdigest()[:8]


def _with_hash(key: str, canonical_path: str) -> str:
    # Reserve nine ASCII bytes for the suffix; discard only an incomplete
    # trailing UTF-8 character so the readable prefix remains valid Unicode.
    prefix = key.encode("utf-8")[:MAX_WORKSPACE_KEY_BYTES - 9].decode("utf-8", errors="ignore")
    return f"{prefix}-{_short_hash(canonical_path)}"


def workspace_key(canonical_path: str) -> str:
    """Encode an already normalized absolute path, preserving readable names."""
    key = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", canonical_path).rstrip(" .") or "workspace"
    return _with_hash(key, canonical_path) if len(key.encode("utf-8")) > MAX_WORKSPACE_KEY_BYTES else key


def checked_path(directory: Path, name: str | None = None) -> Path:
    """Internal storage is outside the workspace, but must not follow aliases."""
    path = directory if name is None else directory / name
    for candidate in (directory.parent, directory, path):
        if candidate.is_symlink() or candidate.is_junction():
            raise InvalidHandoffError("handoff storage paths must not be symlinks or junctions")
    return path


def publish_bytes(path: Path, data: bytes) -> None:
    """Publish a complete file exclusively; never replace an existing file."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".handoff-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # A same-directory hard link atomically exposes all bytes without overwrite.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def storage_directory(root: Path, user_data: Path, *, create: bool = False) -> Path:
    """Resolve ownership from workspace.json, without caching workspace state."""
    canonical = str(root)
    key = workspace_key(canonical)
    base = user_data / "handoffs"
    candidates = [checked_path(base / name) for name in dict.fromkeys((key, _with_hash(key, canonical)))]
    # Reuse existing ownership before choosing a missing primary or fallback.
    for directory in sorted(candidates, key=lambda path: not path.exists()):
        metadata_path = checked_path(directory, "workspace.json")
        if not directory.exists():
            if not create:
                return directory
            directory.mkdir(parents=True, exist_ok=True)
            metadata = json.dumps(
                {"canonical_path": canonical, "display_name": root.name or None},
                ensure_ascii=False, indent=2,
            ).encode("utf-8") + b"\n"
            try:
                publish_bytes(metadata_path, metadata)
            except FileExistsError:
                pass  # Another resolver published ownership first; verify it below.
            except OSError:
                # Leave no unclaimed empty directory after a failed first write.
                try:
                    directory.rmdir()
                except OSError:
                    pass
                raise
        if not directory.is_dir():
            raise InvalidHandoffError(f"handoff storage must be a directory: {directory}")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            owner = metadata["canonical_path"]
            if not isinstance(owner, str) or not Path(owner).is_absolute():
                raise ValueError("canonical_path must be an absolute path")
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise InvalidHandoffError(f"invalid workspace.json: {metadata_path}: {error}") from error
        if os.path.normcase(owner) == os.path.normcase(canonical):
            return directory
    raise InvalidHandoffError(f"workspace-key collision at {directory}")
