"""Execute registered tools with this device's own configuration and permissions."""

from collections.abc import Awaitable, Callable
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from chat2local.handoff.store import (
    HandoffStore, SUMMARY_MAX_LENGTH, TITLE_MAX_LENGTH,
    validate_summary, validate_title, validate_workstream,
)
from chat2local.handoff.models import HandoffError

from chat2local.runtime.config import AppConfig, validation_message
from chat2local.runtime.process_manager import ProcessManager, ProcessError, UnknownProcessError
from chat2local.runtime.workspace import WorkspaceManager, WorkspaceError
from chat2local.runtime.shell import ShellError
from chat2local.tools.apply_patch import PatchError, apply_patch as apply_patch_impl
from chat2local.tools.exec_command import exec_command as exec_command_impl
from chat2local.tools.interact_process import interact_process as interact_process_impl
from chat2local.tools.kill_process import kill_process as kill_process_impl
from chat2local.tools.read import ReadError, read as read_impl
from chat2local.tools.search import SearchError, search as search_impl

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
logger = logging.getLogger(__name__)


class ToolExecutionError(ValueError):
    """A readable local tool failure, also transported to remote callers."""


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _WorkspaceArguments(_Arguments):
    workspace: str | None = None


class ReadArguments(_WorkspaceArguments):
    path: str
    start_line: int | None = None
    end_line: int | None = None


class SearchArguments(_WorkspaceArguments):
    query: str
    mode: str
    path: str = "."
    include: list[str] | None = None
    exclude: list[str] | None = None
    regex: bool = False
    case_sensitive: bool = False
    max_results: int | None = None


class PatchArguments(_WorkspaceArguments):
    patch: str


class ExecCommandArguments(_WorkspaceArguments):
    command: str
    cwd: str = "."


class InteractProcessArguments(_Arguments):
    process_id: str
    input: str | None = None


class KillProcessArguments(_Arguments):
    process_id: str


class HandoffListArguments(_WorkspaceArguments):
    pass


class HandoffGetArguments(_WorkspaceArguments):
    workstream: str

    @field_validator("workstream")
    @classmethod
    def valid_workstream(cls, value: str) -> str:
        return validate_workstream(value)


class HandoffSaveArguments(HandoffGetArguments):
    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    summary: str = Field(min_length=1, max_length=SUMMARY_MAX_LENGTH)
    content: str
    expected_revision: int = Field(ge=0)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        return validate_title(value)

    @field_validator("summary")
    @classmethod
    def valid_summary(cls, value: str) -> str:
        return validate_summary(value)


class LocalToolDispatcher:
    def __init__(
        self, workspace: WorkspaceManager, config: AppConfig,
        process_manager: ProcessManager | None = None,
    ) -> None:
        self.workspace = workspace
        self.config = config
        self.handoff_store = HandoffStore()
        self.process_manager = process_manager if process_manager is not None else ProcessManager(config.process)
        self._handlers: dict[str, ToolHandler] = {}
        self.register("read", self._read)
        self.register("search", self._search)
        self.register("apply_patch", self._apply_patch)
        self.register("exec_command", self._exec_command)
        self.register("interact_process", self._interact_process)
        self.register("kill_process", self._kill_process)
        self.register("handoff_list", self._handoff_list)
        self.register("handoff_get", self._handoff_get)
        self.register("handoff_save", self._handoff_save)

    @property
    def tools(self) -> tuple[str, ...]:
        return tuple(self._handlers)

    def register(self, name: str, handler: ToolHandler) -> None:
        if not name or name in self._handlers:
            raise ValueError(f"Invalid or duplicate local tool: {name}")
        self._handlers[name] = handler

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise ToolExecutionError(f"Unknown local tool: {tool_name}")
        try:
            return await handler(arguments)
        except ValidationError as error:
            raise ToolExecutionError(f"Invalid arguments for {tool_name}: {validation_message(error)}") from None
        except UnknownProcessError as error:
            raise ToolExecutionError("unknown_process: Unknown managed process_id") from error
        except (ToolExecutionError, WorkspaceError, ShellError, HandoffError, PatchError,
                ReadError, SearchError, OSError, ProcessError) as error:
            raise ToolExecutionError(str(error)) from error
        except Exception:
            logger.exception("Unexpected local tool failure")
            raise ToolExecutionError("Internal local tool error") from None

    def _workspace(self, selected: str | None) -> WorkspaceManager:
        return self.workspace if selected is None else self.workspace.select_workspace(selected)

    async def _handoff_list(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = HandoffListArguments.model_validate(arguments)
        return await self.handoff_store.list(self._workspace(args.workspace))

    async def _handoff_get(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = HandoffGetArguments.model_validate(arguments)
        return await self.handoff_store.get(self._workspace(args.workspace), args.workstream)

    async def _handoff_save(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = HandoffSaveArguments.model_validate(arguments)
        return await self.handoff_store.save(
            self._workspace(args.workspace), args.workstream, args.title, args.summary,
            args.content, args.expected_revision,
        )

    async def _exec_command(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = ExecCommandArguments.model_validate(arguments)
        return await exec_command_impl(
            self.process_manager, self._workspace(args.workspace), args.command, cwd=args.cwd,
        )

    async def _interact_process(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = InteractProcessArguments.model_validate(arguments)
        return await interact_process_impl(self.process_manager, args.process_id, input=args.input)

    async def _kill_process(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = KillProcessArguments.model_validate(arguments)
        return await kill_process_impl(self.process_manager, args.process_id)

    async def _apply_patch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = PatchArguments.model_validate(arguments)
        return await apply_patch_impl(self._workspace(args.workspace), args.patch)

    async def _read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = ReadArguments.model_validate(arguments)
        return await read_impl(
            self._workspace(args.workspace), args.path,
            start_line=args.start_line, end_line=args.end_line,
            max_lines=self.config.read.max_lines, max_bytes=self.config.read.max_bytes,
        )

    async def _search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = SearchArguments.model_validate(arguments)
        return await search_impl(
            self._workspace(args.workspace), args.query, mode=args.mode, path=args.path,
            include=args.include, exclude=args.exclude, regex=args.regex,
            case_sensitive=args.case_sensitive,
            max_results=self.config.search.max_results if args.max_results is None else args.max_results,
            timeout=self.config.search.timeout,
        )
