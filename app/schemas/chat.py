"""OpenAI-compatible chat completion request and response models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """A single chat message."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ChatCompletionRequest(BaseModel):
    """Request body accepted by the chat completions endpoint."""

    model_config = ConfigDict(extra="ignore")

    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    stop: str | list[str] | None = None
    stream: bool = False


class ChatCompletionChoice(BaseModel):
    """One generated assistant choice."""

    index: int = 0
    message: ChatMessage
    finish_reason: Literal["stop", "length", "tool_calls"] | None = "stop"


class Usage(BaseModel):
    """Token usage reported by a provider."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    """Normalized non-streaming chat completion response."""

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: Usage


class ChatCompletionChunkDelta(BaseModel):
    """Incremental assistant text in an OpenAI-compatible stream chunk."""

    role: Literal["assistant"] | None = None
    content: str | None = None


class ChatCompletionChunkChoice(BaseModel):
    """One streamed choice delta."""

    index: int = 0
    delta: ChatCompletionChunkDelta
    finish_reason: Literal["stop"] | None = None


class ChatCompletionChunk(BaseModel):
    """OpenAI-compatible server-sent chat completion chunk."""

    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChatCompletionChunkChoice]
