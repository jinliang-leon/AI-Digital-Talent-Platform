"""Shared runtime for structured model calls and scrubbed tracing."""
import json

import httpx
from fastapi import HTTPException

from .ai_client import ModelError, OpenAICompatible


def invoke(settings, trace, name, schema, system, data, *, tool=False):
    if not settings.external_ai_enabled or not (settings.chat_model and settings.model_url and settings.model_key):
        raise ModelError("AI_PROVIDER_NOT_CONFIGURED")
    with trace.step(name, model=settings.chat_model, prompt_version="v1") as metadata:
        with httpx.Client() as client:
            model = OpenAICompatible(client, settings.model_url, settings.model_key, settings.chat_model,
                                     timeout=settings.model_timeout)
            call = model.call_tool if tool else model.structured
            result = call([
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
            ], schema)
        metadata.update(prompt_tokens=result.usage.prompt_tokens, completion_tokens=result.usage.completion_tokens)
    return result.value


def model_http_error(exc):
    return HTTPException(503 if exc.retryable or "CONFIG" in exc.code else 502, exc.code)
