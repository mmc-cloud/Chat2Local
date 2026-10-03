"""One result shape for device-local Process Tools; cursors stay in the runtime."""

from typing import Any

from chat2local.runtime.process_manager import ProcessOutput


def serialize_output(output: ProcessOutput) -> dict[str, Any]:
    return {
        "process_id": output.status.process_id,
        "state": output.status.state,
        "exit_code": output.status.exit_code,
        "draining": output.status.draining,
        "stdout": output.stdout.text,
        "stderr": output.stderr.text,
        "stdout_has_more": output.stdout.has_more,
        "stderr_has_more": output.stderr.has_more,
        "stdout_dropped": output.stdout.output_dropped,
        "stderr_dropped": output.stderr.output_dropped,
    }
