"""Handoff records and stable, transport-independent error prefixes."""

from dataclasses import dataclass


class HandoffError(ValueError):
    """A readable handoff failure."""


class InvalidWorkstreamError(HandoffError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"invalid_workstream: {detail}")


class HandoffNotFoundError(HandoffError):
    def __init__(self, workstream: str) -> None:
        super().__init__(f"handoff_not_found: {workstream}")


class InvalidHandoffError(HandoffError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"invalid_handoff: {detail}")


class RevisionConflictError(HandoffError):
    def __init__(self, expected: int, current: int) -> None:
        super().__init__(f"revision_conflict: expected revision {expected}, current revision {current}")


@dataclass(frozen=True)
class HandoffMetadata:
    workstream: str
    revision: int
    updated_at: str
    title: str
    summary: str


@dataclass(frozen=True)
class HandoffRecord(HandoffMetadata):
    content: str
