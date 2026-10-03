"""Small, typed JSON messages shared by Hub and Agent."""

from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROTOCOL_VERSION = 1
HELLO_TIMEOUT = 10.0


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol_version: Literal[1]


class Hello(Message):
    type: Literal["hello"] = "hello"
    device_id: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")
    token: str = Field(min_length=1, repr=False)
    capabilities: list[str]


class HelloAck(Message):
    type: Literal["hello_ack"] = "hello_ack"
    ok: bool
    error: str | None = None


class Request(Message):
    type: Literal["request"] = "request"
    request_id: UUID
    tool: str = Field(min_length=1)
    arguments: dict[str, Any]


class Response(Message):
    type: Literal["response"] = "response"
    request_id: UUID
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.ok and (self.result is None or self.error is not None):
            raise ValueError("Successful response requires result and no error")
        if not self.ok and (not self.error or self.result is not None):
            raise ValueError("Failed response requires error and no result")
        return self
