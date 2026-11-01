"""Read-only image preview capability, bounded before reading or base64 encoding."""

import asyncio
import base64
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from chat2local.browser.bridge import failure
from chat2local.runtime.workspace import WorkspaceError, WorkspaceManager

CAPABILITY = "file.preview"
MAX_IMAGE_BYTES = 512 * 1024
RESULT_CONTRACT = {
    "success": "An MCP image content block plus metadata: path, mime_type and size.",
    "limits": "Reads at most the selected device's read.max_bytes or 512 KiB, whichever is smaller. No caller limit override.",
    "formats": "image/png, image/jpeg, image/gif, image/webp, image/bmp; sniffed from signatures, not extensions.",
    "errors": "outside_workspace, not_found, not_regular_file, preview_too_large, unsupported_format, read_failed.",
    "scope": "Read-only within the selected workspace. No converter, download endpoint or temporary file.",
}


class PreviewArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=4096, description="Image file path inside the selected workspace.")
    workspace: str | None = Field(default=None, description="Optional authorized absolute workspace on the selected device.")


def _mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return None


def _preview(workspace: WorkspaceManager, arguments: PreviewArguments, max_bytes: int) -> dict[str, Any]:
    try:
        selected = workspace if arguments.workspace is None else workspace.select_workspace(arguments.workspace)
        path = selected.resolve_path(arguments.path)
        if not path.exists():
            return failure("not_found", "Preview file does not exist")
        if not path.is_file():
            return failure("not_regular_file", "Preview requires a regular image file")
        limit = min(max_bytes, MAX_IMAGE_BYTES)
        if path.stat().st_size > limit:
            return failure("preview_too_large", "Image exceeds the device preview budget; resize it locally before retrying")
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
        if len(data) > limit:
            return failure("preview_too_large", "Image grew beyond the device preview budget")
        mime = _mime(data)
        if mime is None:
            return failure("unsupported_format", "Inline preview supports PNG, JPEG, GIF, WebP and BMP by signature; SVG, PDF and Office are not rendered")
        return {"ok": True, "result": {"path": path.relative_to(selected.root).as_posix(),
                                          "mime_type": mime, "size": len(data)},
                "_content": [{"type": "image", "mimeType": mime,
                              "data": base64.b64encode(data).decode("ascii")}]}
    except WorkspaceError:
        return failure("outside_workspace", "Preview file or selected workspace is outside the device's authorization")
    except (OSError, ValueError, RuntimeError):
        return failure("read_failed", "Could not read the preview file")


async def preview(workspace: WorkspaceManager, arguments: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    return await asyncio.to_thread(_preview, workspace, PreviewArguments.model_validate(arguments), max_bytes)
