"""Embedding provider plus an offline, explicitly labeled lexical vector mode."""
from collections import Counter
from hashlib import blake2b
from math import isfinite, sqrt
import re
from urllib.parse import urlsplit

import httpx

from .ai_client import ModelError


def terms(text):
    words = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", text.casefold())
    for word in words:
        if word[0].isascii():
            yield word
        else:
            yield from word
            yield from (word[i:i + 2] for i in range(len(word) - 1))


def normalize(values):
    norm = sqrt(sum(value * value for value in values))
    return [value / norm for value in values] if norm else values


def lexical_vector(text):
    vector = [0.0] * 256
    for word, count in Counter(terms(text)).items():
        bucket = int.from_bytes(blake2b(word.encode(), digest_size=4).digest(), "big") % len(vector)
        vector[bucket] += 1 + min(count - 1, 3) / 3
    return normalize(vector)


def embed(settings, texts, *, authorized=False):
    model = settings.embedding_model
    if not model or not authorized:
        return "lexical-hash-256-v1", [lexical_vector(text) for text in texts]
    if not settings.model_key:
        raise ModelError("MODEL_CONFIGURATION_INVALID")
    url = urlsplit(settings.model_url)
    if (not url.hostname or url.username or url.password or url.query or url.fragment
            or (url.scheme != "https" and (url.scheme != "http" or url.hostname not in
                                             {"127.0.0.1", "::1", "localhost"}))):
        raise ModelError("MODEL_CONFIGURATION_INVALID")
    try:
        response = httpx.post(settings.model_url.rstrip("/") + "/embeddings", follow_redirects=False,
                              headers={"Authorization": "Bearer " + settings.model_key},
                              json={"model": model, "input": texts}, timeout=httpx.Timeout(35, connect=5))
    except httpx.TransportError as exc:
        raise ModelError("EMBEDDING_TRANSPORT_FAILED", retryable=True) from exc
    if not response.is_success:
        raise ModelError("EMBEDDING_HTTP_FAILURE",
                         retryable=response.status_code in {408, 429} or response.status_code >= 500)
    try:
        rows = sorted(response.json()["data"], key=lambda row: row["index"])
        vectors = [row["embedding"] for row in rows]
        dimensions = len(vectors[0])
        if ([row["index"] for row in rows] != list(range(len(texts))) or not 1 <= dimensions <= 4096
                or any(not isinstance(vector, list) or len(vector) != dimensions
                       or not all(type(value) in (float, int) and isfinite(value) and abs(value) <= 1e6
                                  for value in vector) or not any(vector) for vector in vectors)):
            raise ValueError("embedding schema")
    except (KeyError, IndexError, ValueError, TypeError, OverflowError) as exc:
        raise ModelError("EMBEDDING_OUTPUT_INVALID") from exc
    return model, [normalize(vector) for vector in vectors]
