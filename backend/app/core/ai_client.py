"""OpenAI-compatible model transport with explicit error and usage contracts."""
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel


class ModelError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class StructuredResult:
    value: BaseModel
    usage: Usage


class OpenAICompatible:
    """The caller owns the HTTP client and decides when data may leave the system."""

    def __init__(self, client: httpx.Client, base_url: str, api_key: str, model: str, *, timeout: float = 35):
        endpoint = urlsplit(base_url)
        local = endpoint.hostname in {"127.0.0.1", "::1", "localhost"}
        valid_scheme = endpoint.scheme == "https" or (local and endpoint.scheme == "http")
        if (not valid_scheme or not endpoint.hostname or endpoint.username or endpoint.password
                or endpoint.fragment or endpoint.query or not api_key or not model):
            raise ModelError("MODEL_CONFIGURATION_INVALID")
        self.client = client
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = httpx.Timeout(timeout, connect=5)

    def structured(self, messages: list[dict[str, str]], schema: type[BaseModel]) -> StructuredResult:
        return self._request(messages, schema)

    def call_tool(self, messages: list[dict[str, str]], schema: type[BaseModel]) -> StructuredResult:
        return self._request(messages, schema, tool=True)

    def _request(self, messages, schema, *, tool=False):
        if tool:
            options = {"tools": [{"type": "function", "function": {
                "name": schema.__name__, "description": "Choose one authorized read-only recruitment action",
                "parameters": schema.model_json_schema(),
            }}], "tool_choice": {"type": "function", "function": {"name": schema.__name__}}}
        else:
            options = {"response_format": {"type": "json_schema", "json_schema": {
                "name": schema.__name__, "strict": True, "schema": schema.model_json_schema(),
            }}}
        try:
            response = self.client.post(
                self.url, headers={"Authorization": "Bearer " + self.api_key},
                follow_redirects=False, timeout=self.timeout,
                json={
                    "model": self.model, "messages": messages, "temperature": 0, "max_tokens": 3072, **options,
                },
            )
        except httpx.TimeoutException as exc:
            raise ModelError("MODEL_TIMEOUT", retryable=True) from exc
        except httpx.TransportError as exc:
            raise ModelError("MODEL_TRANSPORT_FAILED", retryable=True) from exc
        if not response.is_success:
            retryable = response.status_code in {408, 429} or response.status_code >= 500
            raise ModelError("MODEL_HTTP_FAILURE", retryable=retryable)
        try:
            payload = response.json()
            choice = payload["choices"][0]
            message = choice["message"]
            if message.get("refusal"):
                raise ModelError("MODEL_REFUSAL")
            if choice.get("finish_reason") != ("tool_calls" if tool else "stop"):
                raise ModelError("MODEL_INCOMPLETE")
            if tool:
                calls = message["tool_calls"]
                if len(calls) != 1 or calls[0]["function"]["name"] != schema.__name__:
                    raise ModelError("MODEL_TOOL_INVALID")
                content = calls[0]["function"]["arguments"]
            else:
                content = message["content"]
            value = schema.model_validate_json(content)
            usage = payload.get("usage") or {}
            return StructuredResult(value, Usage(
                _token_count(usage.get("prompt_tokens")), _token_count(usage.get("completion_tokens")),
            ))
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelError("MODEL_OUTPUT_INVALID") from exc


def _token_count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None
