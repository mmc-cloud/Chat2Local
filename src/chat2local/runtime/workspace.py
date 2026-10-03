from collections.abc import Sequence
from pathlib import Path


class WorkspaceError(ValueError):
    pass


class WorkspaceManager:
    def __init__(
        self,
        workspace: str | Path,
        *,
        allowed_roots: Sequence[str | Path] | None = None,
    ) -> None:
        root = Path(workspace).expanduser().resolve()

        if not root.exists():
            raise WorkspaceError(f"Workspace does not exist: {root}")

        if not root.is_dir():
            raise WorkspaceError(f"Workspace is not a directory: {root}")

        resolved_roots = tuple(
            Path(allowed_root).expanduser().resolve()
            for allowed_root in (allowed_roots or [root])
        )

        for allowed_root in resolved_roots:
            if not allowed_root.exists():
                raise WorkspaceError(f"Allowed root does not exist: {allowed_root}")
            if not allowed_root.is_dir():
                raise WorkspaceError(f"Allowed root is not a directory: {allowed_root}")

        if not any(root.is_relative_to(allowed_root) for allowed_root in resolved_roots):
            raise WorkspaceError(f"Workspace is outside allowed roots: {root}")

        self.root = root
        self.allowed_roots = resolved_roots

    def select_workspace(self, workspace: str | Path) -> "WorkspaceManager":
        """Select a workspace for one call without changing the default or policy."""

        if not Path(workspace).is_absolute():
            raise WorkspaceError(f"Tool workspace must be an absolute path: {workspace}")

        return WorkspaceManager(workspace, allowed_roots=self.allowed_roots)

    def relative_path(self, path: str | Path) -> str:
        """Return the workspace-relative POSIX path used in Tool results."""

        return self.resolve_path(path).relative_to(self.root).as_posix()

    def resolve_path(self, path: str | Path) -> Path:
        target = Path(path).expanduser()

        if not target.is_absolute():
            target = self.root / target

        resolved = target.resolve()

        if not resolved.is_relative_to(self.root):
            raise WorkspaceError(
                f"Path is outside workspace: {path}"
            )

        return resolved
