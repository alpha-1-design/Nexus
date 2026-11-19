"""Unified AI Provider Layer for Nexus.

Supports: OpenAI, Anthropic, Google Gemini, Groq, DeepSeek, Ollama, LM Studio,
and any OpenAI-compatible endpoint.
"""

import json
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class ModelInfo:
    """Information about a model."""

    id: str
    name: str
    provider: str
    context_window: int = 128000
    supports_vision: bool = False
    supports_function_calling: bool = True
    supports_streaming: bool = True
    max_output_tokens: int | None = None


@dataclass
class ToolCall:
    """Represents a tool call from the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    """A message in the conversation."""

    role: str  # system, user, assistant, tool
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list | None = None


@dataclass
class Response:
    """A response from the model."""

    content: str
    model: str
    finish_reason: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] | None = None


@dataclass
class StreamChunk:
    """A chunk from a streaming response."""

    content: str = ""
    tool_call: ToolCall | None = None
    finish_reason: str | None = None
    done: bool = False


class BaseProvider(ABC):
    """Base class for all AI providers."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._client: httpx.AsyncClient | None = None

    @abstractmethod
    async def complete(self, messages: list[Message], tools: list[dict[str, Any]] | None = None, **kwargs) -> Response:
        """Generate a completion."""
        pass

    @abstractmethod
    async def stream(self, messages: list[Message], tools: list[dict[str, Any]] | None = None, **kwargs) -> AsyncIterator[StreamChunk]:
        """Generate a streaming completion."""
        pass

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """List available models."""
        pass

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()


class _StreamToolCallBuffer:
    """Accumulates streamed tool-call fragments into complete ToolCalls.

    OpenAI-compatible providers stream ``tool_calls[].function.arguments``
    INCREMENTALLY as partial JSON string deltas, so parsing per chunk always
    fails. Fragments are buffered per tool-call index and only parsed once the
    stream signals finish (``finish_reason="tool_calls"`` or the ``[DONE]``
    marker).
    """

    def __init__(self):
        # Pending fragments keyed by the tool-call index assigned by the provider.
        self._pending: dict[int, dict[str, str]] = {}

    def feed(self, delta_tool_calls: list[dict[str, Any]]) -> None:
        """Buffer one chunk's tool_calls deltas (appending argument fragments)."""
        for tc in delta_tool_calls:
            index = tc.get("index", 0)
            entry = self._pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if tc.get("id"):
                entry["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                entry["name"] = fn["name"]
            entry["arguments"] += fn.get("arguments") or ""

    def flush(self) -> list[ToolCall]:
        """Parse buffered fragments into complete ToolCalls (in index order)."""
        calls: list[ToolCall] = []
        for index in sorted(self._pending):
            entry = self._pending[index]
            arguments: dict[str, Any] = {}
            if entry["arguments"]:
                try:
                    parsed = json.loads(entry["arguments"])
                    if isinstance(parsed, dict):
                        arguments = parsed
                except json.JSONDecodeError:
                    # Malformed JSON from the provider: execute with no args and
                    # let the tool report the missing-argument error.
                    arguments = {}
            calls.append(
                ToolCall(
                    id=entry["id"] or f"call_{index}",
                    name=entry["name"],
                    arguments=arguments,
                )
            )
        self._pending.clear()
        return calls


class OpenAIProvider(BaseProvider):
    """OpenAI API provider (also OpenAI-compatible endpoints)."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.base_url = config.get("base_url", "https://api.openai.com/v1")
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "gpt-4o")
        self.timeout = config.get("timeout", 120)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def complete(self, messages: list[Message], tools: list[dict[str, Any]] | None = None, **kwargs) -> Response:
        client = self._get_client()

        payload: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "messages": [self._format_message(m) for m in messages],
            "max_tokens": kwargs.get("max_tokens", 4096),
            "temperature": kwargs.get("temperature", 0.7),
            "stream": False,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        response = await client.post("/chat/completions", json=payload)
        if response.status_code != 200:
            response.raise_for_status()
        data = response.json()

        return self._parse_response(data)

    async def stream(self, messages: list[Message], tools: list[dict[str, Any]] | None = None, **kwargs) -> AsyncIterator[StreamChunk]:
        client = self._get_client()

        payload: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "messages": [self._format_message(m) for m in messages],
            "max_tokens": kwargs.get("max_tokens", 4096),
            "temperature": kwargs.get("temperature", 0.7),
            "stream": True,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()

        # Tool-call arguments arrive incrementally as partial JSON fragments, so
        # they are buffered per tool-call index and only parsed once the stream
        # signals finish. Complete ToolCalls are emitted right before the done
        # chunk, keeping StreamChunk's shape compatible with all consumers.
        buffer = _StreamToolCallBuffer()
        finish_reason: str | None = None
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break

            chunk_data = json.loads(data)
            choices = chunk_data.get("choices") or [{}]
            delta = (choices[0] or {}).get("delta") or {}
            if delta.get("tool_calls"):
                buffer.feed(delta["tool_calls"])

            chunk = self._parse_stream_chunk(chunk_data)
            if chunk.content:
                yield chunk
            if chunk.finish_reason:
                # Provider signalled the end of the stream; stop reading here.
                finish_reason = chunk.finish_reason
                break

        # Flush complete tool calls (parsed once, on stream finish) before done.
        for tool_call in buffer.flush():
            yield StreamChunk(tool_call=tool_call, finish_reason=finish_reason)
        yield StreamChunk(done=True, finish_reason=finish_reason)

    async def list_models(self) -> list[ModelInfo]:
        client = self._get_client()
        response = await client.get("/models")
        response.raise_for_status()
        data = response.json()

        models = []
        for m in data.get("data", []):
            models.append(
                ModelInfo(
                    id=m["id"],
                    name=m.get("name", m["id"]),
                    provider="openai",
                    context_window=m.get("context_window", 128000),
                )
            )
        return models

    def _format_message(self, msg: Message) -> dict[str, Any]:
        result: dict[str, Any] = {"role": msg.role, "content": msg.content}
        if msg.name:
            result["name"] = msg.name
        if msg.tool_call_id:
            result["tool_call_id"] = msg.tool_call_id
        if msg.tool_calls:
            # Assistant tool-call requests must be echoed back in the OpenAI
            # wire format, otherwise role="tool" results are rejected.
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in msg.tool_calls
            ]
        return result

    def _parse_response(self, data: dict[str, Any]) -> Response:
        choice = data["choices"][0]
        message = choice["message"]

        content = message.get("content", "")
        tool_calls = []

        if "tool_calls" in message:
            for tc in message["tool_calls"]:
                tool_calls.append(
                    ToolCall(
                        id=tc["id"],
                        name=tc["function"]["name"],
                        arguments=json.loads(tc["function"]["arguments"]),
                    )
                )

        return Response(
            content=content,
            model=data.get("model", self.model),
            finish_reason=choice.get("finish_reason"),
            tool_calls=tool_calls,
            usage=data.get("usage"),
        )

    def _parse_stream_chunk(self, data: dict[str, Any]) -> StreamChunk:
        """Parse one SSE chunk into a StreamChunk (content/finish_reason only).

        Tool-call deltas are NOT parsed here: their arguments arrive as
        incremental partial-JSON fragments. OpenAIProvider.stream() buffers them
        via _StreamToolCallBuffer and emits complete ToolCalls at stream finish.
        """
        choices = data.get("choices") or [{}]
        choice = choices[0] or {}
        delta = choice.get("delta") or {}

        return StreamChunk(
            content=delta.get("content") or "",
            finish_reason=choice.get("finish_reason"),
            done=choice.get("finish_reason") is not None,
        )


class AnthropicProvider(BaseProvider):
    """Anthropic Claude API provider."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "claude-3-5-sonnet-20241022")
        self.base_url = "https://api.anthropic.com/v1"
        self.timeout = config.get("timeout", 120)
        self.api_version = "2023-06-01"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": self.api_version,
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        return self._client

    def _format_tools(self, tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Convert OpenAI-style tools to Anthropic format."""
        if not tools:
            return []

        anthropic_tools = []
        for tool in tools:
            anthropic_tools.append(
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "input_schema": tool["function"]["parameters"],
                }
            )
        return anthropic_tools

    async def complete(self, messages: list[Message], tools: list[dict[str, Any]] | None = None, **kwargs) -> Response:
        client = self._get_client()

        # Convert messages to Anthropic format
        anthropic_messages = []
        system_prompt = ""

        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
            else:
                anthropic_messages.append(
                    {
                        "role": msg.role,
                        "content": msg.content,
                    }
                )

        payload: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "temperature": kwargs.get("temperature", 0.7),
        }

        if system_prompt:
            payload["system"] = system_prompt

        if tools:
            payload["tools"] = self._format_tools(tools)

        response = await client.post("/messages", json=payload)
        response.raise_for_status()
        data = response.json()

        return self._parse_response(data)

    async def stream(self, messages: list[Message], tools: list[dict[str, Any]] | None = None, **kwargs) -> AsyncIterator[StreamChunk]:
        client = self._get_client()

        anthropic_messages = []
        system_prompt = ""

        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
            else:
                anthropic_messages.append(
                    {
                        "role": msg.role,
                        "content": msg.content,
                    }
                )

        payload: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "temperature": kwargs.get("temperature", 0.7),
            "stream": True,
        }

        if system_prompt:
            payload["system"] = system_prompt

        if tools:
            payload["tools"] = self._format_tools(tools)

        async with client.stream("POST", "/messages", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    yield StreamChunk(done=True)
                    return

                chunk_data = json.loads(data)
                yield self._parse_stream_chunk(chunk_data)

    async def list_models(self) -> list[ModelInfo]:
        # Anthropic doesn't have a public model list endpoint
        return [
            ModelInfo(
                id="claude-opus-4-5",
                name="Claude Opus 4",
                provider="anthropic",
                context_window=200000,
                supports_vision=True,
            ),
            ModelInfo(
                id="claude-sonnet-4-5",
                name="Claude Sonnet 4",
                provider="anthropic",
                context_window=200000,
                supports_vision=True,
            ),
            ModelInfo(
                id="claude-haiku-4-5",
                name="Claude Haiku 4",
                provider="anthropic",
                context_window=200000,
                supports_vision=True,
            ),
        ]

    def _parse_response(self, data: dict[str, Any]) -> Response:
        content = ""
        tool_calls = []

        for block in data.get("content", []):
            if block["type"] == "text":
                content += block["text"]
            elif block["type"] == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block["id"],
                        name=block["name"],
                        arguments=block["input"],
                    )
                )

        return Response(
            content=content,
            model=data.get("model", self.model),
            finish_reason=data.get("stop_reason"),
            tool_calls=tool_calls,
            usage=data.get("usage"),
        )

    def _parse_stream_chunk(self, data: dict[str, Any]) -> StreamChunk:
        content = ""
        tool_call = None

        for block in data.get("content", []):
            if block["type"] == "text":
                content += block["text"]
            elif block["type"] == "tool_use":
                tool_call = ToolCall(
                    id=block["id"],
                    name=block["name"],
                    arguments=block["input"],
                )

        return StreamChunk(
            content=content,
            tool_call=tool_call,
            done=data.get("type") == "message_stop",
        )


class GeminiProvider(BaseProvider):
    """Google Gemini API provider."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "gemini-2.0-flash")
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        self.timeout = config.get("timeout", 120)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
            )
        return self._client

    def _format_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert messages to Gemini format."""
        contents = []
        system_instruction = ""

        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content
            else:
                role = "user" if msg.role == "user" else "model"
                contents.append(
                    {
                        "role": role,
                        "parts": [{"text": msg.content}],
                    }
                )

        return contents, system_instruction

    def _format_tools(self, tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        """Convert OpenAI-style tools to Gemini format."""
        if not tools:
            return {}

        function_declarations = []
        for tool in tools:
            function_declarations.append(tool["function"])

        return {"function_declarations": function_declarations}

    async def complete(self, messages: list[Message], tools: list[dict[str, Any]] | None = None, **kwargs) -> Response:
        client = self._get_client()
        model = kwargs.get("model", self.model)

        contents, system_instruction = self._format_messages(messages)

        payload: dict[str, Any] = {
            "contents": contents,
            "generation_config": {
                "max_output_tokens": kwargs.get("max_tokens", 4096),
                "temperature": kwargs.get("temperature", 0.7),
            },
        }

        if system_instruction:
            payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

        if tools:
            payload["tools"] = self._format_tools(tools)

        url = f"{self.base_url}/models/{model}:generateContent?key={self.api_key}"
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()

        return self._parse_response(data)

    async def stream(self, messages: list[Message], tools: list[dict[str, Any]] | None = None, **kwargs) -> AsyncIterator[StreamChunk]:
        client = self._get_client()
        model = kwargs.get("model", self.model)

        contents, system_instruction = self._format_messages(messages)

        payload: dict[str, Any] = {
            "contents": contents,
            "generation_config": {
                "max_output_tokens": kwargs.get("max_tokens", 4096),
                "temperature": kwargs.get("temperature", 0.7),
            },
        }

        if system_instruction:
            payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

        if tools:
            payload["tools"] = self._format_tools(tools)

        url = f"{self.base_url}/models/{model}:streamGenerateContent?key={self.api_key}&alt=sse"

        async with client.stream("POST", url, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if not data:
                    continue

                chunk_data = json.loads(data)
                yield self._parse_stream_chunk(chunk_data)

    async def list_models(self) -> list[ModelInfo]:
        client = self._get_client()
        url = f"{self.base_url}/models?key={self.api_key}"

        try:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

            models = []
            for m in data.get("models", []):
                models.append(
                    ModelInfo(
                        id=m["name"].split("/")[-1],
                        name=m.get("displayName", m["name"]),
                        provider="google",
                        context_window=m.get("inputTokenLimit", 32000),
                        supports_vision=True,
                    )
                )
            return models
        except Exception:
            return [
                ModelInfo(id="gemini-2.0-flash", name="Gemini 2.0 Flash", provider="google", context_window=1000000),
                ModelInfo(id="gemini-2.5-pro", name="Gemini 2.5 Pro", provider="google", context_window=1000000),
            ]

    def _parse_response(self, data: dict[str, Any]) -> Response:
        content = ""
        tool_calls = []

        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "text" in part:
                    content += part["text"]
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls.append(
                        ToolCall(
                            id=str(id(fc)),  # Gemini doesn't have IDs
                            name=fc["name"],
                            arguments=fc["args"],
                        )
                    )

        return Response(
            content=content,
            model=data.get("modelVersion", self.model),
            tool_calls=tool_calls,
        )

    def _parse_stream_chunk(self, data: dict[str, Any]) -> StreamChunk:
        content = ""
        tool_call = None

        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "text" in part:
                    content += part["text"]
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_call = ToolCall(
                        id=str(id(fc)),
                        name=fc["name"],
                        arguments=fc["args"],
                    )

        return StreamChunk(
            content=content,
            tool_call=tool_call,
            done=data.get("candidates", [{}])[0].get("finishReason") is not None,
        )


class OllamaProvider(OpenAIProvider):
    """Ollama local models provider (OpenAI-compatible API)."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(
            {
                **config,
                "base_url": config.get("base_url", "http://localhost:11434/v1"),
                "model": config.get("model", "llama3.2"),
            }
        )
        self.provider_type = "ollama"

    async def list_models(self) -> list[ModelInfo]:
        try:
            client = self._get_client()
            response = await client.get("/models")
            response.raise_for_status()
            data = response.json()

            models = []
            for m in data.get("models", []):
                models.append(
                    ModelInfo(
                        id=m.get("name", ""),
                        name=m.get("name", ""),
                        provider="ollama",
                        context_window=m.get("details", {}).get("context_length", 4096),
                    )
                )
            return models
        except Exception:
            return [ModelInfo(id="llama3.2", name="Llama 3.2", provider="ollama", context_window=128000)]


class GroqProvider(OpenAIProvider):
    """Groq API provider (OpenAI-compatible)."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(
            {
                **config,
                "base_url": "https://api.groq.com/openai/v1",
                "model": config.get("model", "mixtral-8x7b-32768"),
            }
        )
        self.provider_type = "groq"

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id="llama-3.3-70b-versatile", name="Llama 3.3 70B", provider="groq", context_window=128000),
            ModelInfo(id="mixtral-8x7b-32768", name="Mixtral 8x7B", provider="groq", context_window=32768),
            ModelInfo(id="gemma2-9b-it", name="Gemma 2 9B", provider="groq", context_window=8192),
        ]


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek API provider (OpenAI-compatible)."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(
            {
                **config,
                "base_url": "https://api.deepseek.com/v1",
                "model": config.get("model", "deepseek-chat"),
            }
        )
        self.provider_type = "deepseek"

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id="deepseek-chat", name="DeepSeek Chat", provider="deepseek", context_window=64000),
            ModelInfo(id="deepseek-coder", name="DeepSeek Coder", provider="deepseek", context_window=64000),
        ]


class MistralProvider(OpenAIProvider):
    """Mistral AI provider (OpenAI-compatible)."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(
            {
                **config,
                "base_url": "https://api.mistral.ai/v1",
                "model": config.get("model", "mistral-large-latest"),
            }
        )
        self.provider_type = "mistral"

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id="mistral-large-latest", name="Mistral Large", provider="mistral", context_window=128000),
            ModelInfo(id="mistral-small-latest", name="Mistral Small", provider="mistral", context_window=32000),
            ModelInfo(id="codestral-latest", name="Codestral", provider="mistral", context_window=32000),
        ]


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter provider (OpenAI-compatible router with many free models).

    Free model ids (the ``:free`` suffix) rotate frequently — verified 2026-11
    via https://github.com/mnfst/awesome-free-llm-apis (17 free models listed,
    ids rotate). ``openrouter/free`` lets OpenRouter pick any currently-free
    model. Free limits: ~20 req/min, 50 req/day.
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(
            {
                **config,
                "base_url": config.get("base_url", "https://openrouter.ai/api/v1"),
                "model": config.get("model", "openrouter/free"),
            }
        )
        self.provider_type = "openrouter"

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id="openrouter/free", name="OpenRouter Free Router (auto)", provider="openrouter", context_window=128000),
            ModelInfo(id="google/gemma-3-27b-it:free", name="Gemma 3 27B (free)", provider="openrouter", context_window=96000),
            ModelInfo(id="deepseek/deepseek-chat-v3:free", name="DeepSeek V3 (free, ids rotate)", provider="openrouter", context_window=64000),
        ]


class CerebrasProvider(OpenAIProvider):
    """Cerebras Inference provider (OpenAI-compatible, free API key).

    Endpoint verified 2026-11: https://inference-docs.cerebras.ai —
    wafer-scale inference; free tier is rate-limited but $0.
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(
            {
                **config,
                "base_url": config.get("base_url", "https://api.cerebras.ai/v1"),
                "model": config.get("model", "llama-3.3-70b"),
            }
        )
        self.provider_type = "cerebras"

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id="llama-3.3-70b", name="Llama 3.3 70B (Cerebras)", provider="cerebras", context_window=128000),
            ModelInfo(id="llama3.1-8b", name="Llama 3.1 8B (Cerebras)", provider="cerebras", context_window=128000),
            ModelInfo(id="qwen-3-32b", name="Qwen 3 32B (Cerebras)", provider="cerebras", context_window=128000),
        ]


class GitHubModelsProvider(OpenAIProvider):
    """GitHub Models provider (free tier via a GitHub token).

    Endpoint verified 2026-11: the GA endpoint is
    ``https://models.github.ai/inference`` with publisher-prefixed model ids
    (``openai/gpt-4o-mini``). The legacy ``models.inference.ai.azure.com``
    endpoint still serves older PATs but is deprecated. Auth: plain Bearer
    GITHUB_TOKEN (classic or fine-grained PAT).
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(
            {
                **config,
                "base_url": config.get("base_url", "https://models.github.ai/inference"),
                "model": config.get("model", "openai/gpt-4o-mini"),
            }
        )
        self.provider_type = "github-models"

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id="openai/gpt-4o-mini", name="GPT-4o mini (GitHub)", provider="github-models", context_window=128000),
            ModelInfo(id="openai/gpt-4.1-mini", name="GPT-4.1 mini (GitHub)", provider="github-models", context_window=128000),
            ModelInfo(id="meta/Llama-3.3-70B-Instruct", name="Llama 3.3 70B (GitHub)", provider="github-models", context_window=128000),
        ]


class HuggingFaceProvider(OpenAIProvider):
    """HuggingFace Inference Providers router (OpenAI-compatible).

    Endpoint verified 2026-11: ``https://router.huggingface.co/v1`` routes to
    every serverless Inference Provider with one HF token (free monthly
    credits). Model ids are HF repo ids (``org/model``).
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(
            {
                **config,
                "base_url": config.get("base_url", "https://router.huggingface.co/v1"),
                "model": config.get("model", "meta-llama/Llama-3.3-70B-Instruct"),
            }
        )
        self.provider_type = "huggingface"

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id="meta-llama/Llama-3.3-70B-Instruct", name="Llama 3.3 70B (HF router)", provider="huggingface", context_window=128000),
            ModelInfo(id="Qwen/Qwen2.5-Coder-32B-Instruct", name="Qwen2.5 Coder 32B (HF router)", provider="huggingface", context_window=128000),
        ]


class CloudflareWorkersAIProvider(OpenAIProvider):
    """Cloudflare Workers AI provider (OpenAI-compatible, free daily neurons).

    Endpoint verified 2026-11 via https://github.com/mnfst/awesome-free-llm-apis:
    the OpenAI-compatible endpoint embeds the account id in the URL —
    ``https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1``.
    Auth: Bearer CLOUDFLARE_API_TOKEN (+ CLOUDFLARE_ACCOUNT_ID env).
    """

    def __init__(self, config: dict[str, Any]):
        base_url = config.get("base_url")
        if not base_url:
            account_id = config.get("account_id") or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
            base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
        super().__init__(
            {
                **config,
                "base_url": base_url,
                "model": config.get("model", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
            }
        )
        self.provider_type = "cloudflare-workers-ai"

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id="@cf/meta/llama-3.3-70b-instruct-fp8-fast", name="Llama 3.3 70B fast (CF)", provider="cloudflare-workers-ai", context_window=24000),
            ModelInfo(id="@cf/openai/gpt-oss-120b", name="GPT-OSS 120B (CF)", provider="cloudflare-workers-ai", context_window=128000),
        ]


class ZhipuProvider(OpenAIProvider):
    """Zhipu GLM open platform (OpenAI-compatible; glm-4-flash is free).

    Endpoint verified 2026-11 via https://github.com/mnfst/awesome-free-llm-apis:
    ``https://open.bigmodel.cn/api/paas/v4`` — Bearer auth with a plain API
    key. International endpoint: ``https://api.z.ai/api/paas/v4``.
    glm-4-flash is the free workhorse; newer GLM-4.x-Flash models are free too.
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(
            {
                **config,
                "base_url": config.get("base_url", "https://open.bigmodel.cn/api/paas/v4"),
                "model": config.get("model", "glm-4-flash"),
            }
        )
        self.provider_type = "zhipu"

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id="glm-4-flash", name="GLM-4 Flash (free)", provider="zhipu", context_window=128000),
            ModelInfo(id="glm-4.7-flash", name="GLM-4.7 Flash (free)", provider="zhipu", context_window=128000),
        ]


@dataclass(frozen=True)
class ProviderMeta:
    """Registry metadata for one provider (UI tables + free rotation use it)."""

    name: str
    base_url: str
    env_keys: tuple[str, ...]
    free: bool = False
    default_models: tuple[str, ...] = ()
    signup_url: str = ""
    description: str = ""


PROVIDER_REGISTRY: dict[str, type[BaseProvider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "google": GeminiProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
    "groq": GroqProvider,
    "deepseek": DeepSeekProvider,
    "mistral": MistralProvider,
    "openrouter": OpenRouterProvider,
    "cerebras": CerebrasProvider,
    "github-models": GitHubModelsProvider,
    "huggingface": HuggingFaceProvider,
    "cloudflare-workers-ai": CloudflareWorkersAIProvider,
    "zhipu": ZhipuProvider,
    "openai-compatible": OpenAIProvider,
    "opencode-zen": OpenAIProvider,
    "opencode-go": OpenAIProvider,
}


# Registry metadata: base_url, env keys, free flag, default models, signup
# URL and a one-line description per provider. Endpoints verified 2026-11
# against https://github.com/mnfst/awesome-free-llm-apis unless noted.
PROVIDER_INFO: dict[str, ProviderMeta] = {
    "openai": ProviderMeta(
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        env_keys=("OPENAI_API_KEY",),
        free=False,
        default_models=("gpt-4o", "gpt-4o-mini"),
        signup_url="https://platform.openai.com/api-keys",
        description="GPT models — paid, industry standard.",
    ),
    "anthropic": ProviderMeta(
        name="Anthropic",
        base_url="https://api.anthropic.com/v1",
        env_keys=("ANTHROPIC_API_KEY",),
        free=False,
        default_models=("claude-sonnet-4-20250514", "claude-haiku-4-5"),
        signup_url="https://console.anthropic.com/settings/keys",
        description="Claude models — paid, top-tier reasoning.",
    ),
    "google": ProviderMeta(
        name="Google AI Studio (Gemini)",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        env_keys=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        free=True,
        default_models=("gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"),
        signup_url="https://aistudio.google.com/app/apikey",
        description="Gemini free tier — strong all-round, generous limits.",
    ),
    "ollama": ProviderMeta(
        name="Ollama (Local)",
        base_url="http://localhost:11434/v1",
        env_keys=(),
        free=True,
        default_models=("llama3.2", "qwen2.5-coder:7b"),
        signup_url="https://ollama.com",
        description="Local models — free, offline, no key needed.",
    ),
    "groq": ProviderMeta(
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        env_keys=("GROQ_API_KEY",),
        free=True,
        default_models=("llama-3.3-70b-versatile", "openai/gpt-oss-120b", "llama-3.1-8b-instant"),
        signup_url="https://console.groq.com/keys",
        description="Llama at absurd speed — generous free tier.",
    ),
    "deepseek": ProviderMeta(
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        env_keys=("DEEPSEEK_API_KEY",),
        free=False,
        default_models=("deepseek-chat", "deepseek-reasoner"),
        signup_url="https://platform.deepseek.com/api_keys",
        description="DeepSeek V3/R1 — paid but very cheap.",
    ),
    "mistral": ProviderMeta(
        name="Mistral AI",
        base_url="https://api.mistral.ai/v1",
        env_keys=("MISTRAL_API_KEY",),
        free=True,
        default_models=("mistral-small-latest", "codestral-latest", "mistral-large-latest"),
        signup_url="https://console.mistral.ai/api-keys/",
        description="La Plateforme free experiment tier (small/codestral).",
    ),
    "openrouter": ProviderMeta(
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        env_keys=("OPENROUTER_API_KEY",),
        free=True,
        default_models=("openrouter/free", "google/gemma-3-27b-it:free", "deepseek/deepseek-chat-v3:free"),
        signup_url="https://openrouter.ai/keys",
        description="Hundreds of models; :free ids rotate — openrouter/free always works.",
    ),
    "cerebras": ProviderMeta(
        name="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        env_keys=("CEREBRAS_API_KEY",),
        free=True,
        default_models=("llama-3.3-70b", "llama3.1-8b", "qwen-3-32b"),
        signup_url="https://cloud.cerebras.ai",
        description="Fastest inference available; free API key, daily limits.",
    ),
    "github-models": ProviderMeta(
        name="GitHub Models",
        base_url="https://models.github.ai/inference",
        env_keys=("GITHUB_TOKEN",),
        free=True,
        default_models=("openai/gpt-4o-mini", "meta/Llama-3.3-70B-Instruct"),
        signup_url="https://github.com/settings/tokens",
        description="Free GitHub-hosted models via a GitHub token (PAT).",
    ),
    "huggingface": ProviderMeta(
        name="HuggingFace Router",
        base_url="https://router.huggingface.co/v1",
        env_keys=("HF_TOKEN",),
        free=True,
        default_models=("meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-Coder-32B-Instruct"),
        signup_url="https://huggingface.co/settings/tokens",
        description="One token routes to all HF Inference Providers; free credits.",
    ),
    "cloudflare-workers-ai": ProviderMeta(
        name="Cloudflare Workers AI",
        base_url="https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        env_keys=("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"),
        free=True,
        default_models=("@cf/meta/llama-3.3-70b-instruct-fp8-fast", "@cf/openai/gpt-oss-120b"),
        signup_url="https://dash.cloudflare.com/profile/api-tokens",
        description="10k free neurons/day; account id lives in the URL.",
    ),
    "zhipu": ProviderMeta(
        name="Zhipu GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        env_keys=("ZHIPUAI_API_KEY",),
        free=True,
        default_models=("glm-4-flash", "glm-4.7-flash"),
        signup_url="https://open.bigmodel.cn",
        description="GLM open platform — glm-4-flash is free.",
    ),
    "opencode-zen": ProviderMeta(
        name="OpenCode Zen",
        base_url="https://opencode.ai/zen/v1",
        env_keys=("OPENCODE_ZEN_API_KEY",),
        free=True,
        default_models=("minimax-m2.5-free", "kimi-k2.5"),
        signup_url="https://opencode.ai/zen",
        description="Zero-cost coding models from OpenCode Zen.",
    ),
    "opencode-go": ProviderMeta(
        name="OpenCode Zen Go",
        base_url="https://opencode.ai/zen/go/v1",
        env_keys=("OPENCODE_GO_API_KEY",),
        free=True,
        default_models=("kimi-k2.5",),
        signup_url="https://opencode.ai/zen",
        description="OpenCode Zen Go-routed free models.",
    ),
    "openai-compatible": ProviderMeta(
        name="OpenAI-compatible",
        base_url="",
        env_keys=(),
        free=False,
        default_models=(),
        signup_url="",
        description="Any OpenAI-compatible endpoint (custom base_url + key).",
    ),
}

# Alias: both registry keys share one metadata entry.
PROVIDER_INFO["gemini"] = PROVIDER_INFO["google"]


async def create_provider(
    provider_type: str,
    config: dict[str, Any],
) -> BaseProvider:
    """Factory function to create a provider instance."""
    provider_class = PROVIDER_REGISTRY.get(provider_type.lower())
    if not provider_class:
        raise ValueError(f"Unknown provider type: {provider_type}")
    return provider_class(config)


async def detect_provider(base_url: str) -> str:
    """Auto-detect the provider type from the base URL."""
    if "ollama" in base_url or "lmstudio" in base_url:
        return "openai-compatible"
    elif "groq" in base_url:
        return "groq"
    elif "deepseek" in base_url:
        return "deepseek"
    elif "mistral" in base_url:
        return "mistral"
    elif "anthropic" in base_url:
        return "anthropic"
    elif "google" in base_url or "generativelanguage" in base_url:
        return "gemini"
    elif "openai" in base_url:
        return "openai"
    else:
        return "openai-compatible"
