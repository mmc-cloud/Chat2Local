"""Spawn once as a managed process, wait briefly, then consume buffered output."""

from typing import Any

from chat2local.runtime.process_manager import ProcessManager
from chat2local.runtime.workspace import WorkspaceManager
from chat2local.tools.process_output import serialize_output


async def exec_command(
    manager: ProcessManager, workspace: WorkspaceManager, command: str, *, cwd: str = ".",
) -> dict[str, Any]:
    record = await manager.spawn(command, workspace=workspace, cwd=cwd)
    await manager.wait(record.process_id)
    return serialize_output(manager.read_output(record.process_id))
