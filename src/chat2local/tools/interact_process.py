"""Read incremental output, optionally after sending exact UTF-8 stdin."""

import asyncio
from typing import Any

from chat2local.runtime.process_manager import ProcessManager, STDIN_RESPONSE_WAIT
from chat2local.tools.process_output import serialize_output


async def interact_process(
    manager: ProcessManager, process_id: str, *, input: str | None = None,
) -> dict[str, Any]:
    if input is not None:
        await manager.write_stdin(process_id, input)
        await asyncio.sleep(STDIN_RESPONSE_WAIT)
    return serialize_output(manager.read_output(process_id))
