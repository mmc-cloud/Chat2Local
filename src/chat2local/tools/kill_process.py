"""Terminate a managed process tree without removing its retained output."""

from typing import Any

from chat2local.runtime.process_manager import ProcessManager
from chat2local.tools.process_output import serialize_output


async def kill_process(manager: ProcessManager, process_id: str) -> dict[str, Any]:
    result = await manager.terminate(process_id)
    return {"outcome": result.outcome, **serialize_output(manager.read_output(process_id))}
