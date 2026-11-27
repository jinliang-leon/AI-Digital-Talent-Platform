"""Emit allowlisted workflow metadata to Langfuse over OTLP/HTTP."""
from contextlib import contextmanager
import logging
import re
from time import time_ns
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)
NUMERIC_FIELDS = {"attempt", "blocks", "bytes", "chunks", "completion_tokens", "hits", "prompt_tokens", "results"}
LABEL_FIELDS = {"embedding_revision", "error_code", "intent", "model", "prompt_version", "task_kind", "task_status"}
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:.\-/]{0,95}")


def safe_attributes(metadata):
    attributes = []
    for key, value in metadata.items():
        if key in NUMERIC_FIELDS and type(value) is int and 0 <= value <= 2**53 - 1:
            attributes.append({"key": "talent." + key, "value": {"intValue": str(value)}})
        elif key in LABEL_FIELDS and isinstance(value, str) and LABEL.fullmatch(value):
            attributes.append({"key": "talent." + key, "value": {"stringValue": value}})
    return attributes


class Trace:
    def __init__(self, settings, trace_id):
        self.settings = settings
        self.trace_id = trace_id
        self.spans = []
        self.parents = []

    @contextmanager
    def step(self, name, **metadata):
        started = time_ns()
        status = 1
        parent_id = self.parents[-1] if self.parents else None
        span_id = uuid4().hex[:16]
        self.parents.append(span_id)
        try:
            yield metadata
        except Exception as exc:
            status = 2
            metadata["error_code"] = getattr(exc, "code", type(exc).__name__)
            raise
        finally:
            self.parents.pop()
            span = {
                "traceId": self.trace_id, "spanId": span_id, "name": name,
                "kind": 1, "startTimeUnixNano": str(started), "endTimeUnixNano": str(time_ns()),
                "status": {"code": status},
                "attributes": safe_attributes(metadata),
            }
            if parent_id:
                span["parentSpanId"] = parent_id
            self.spans.append(span)

    def flush(self):
        settings = self.settings
        if not self.spans or not (settings.langfuse_url and settings.langfuse_public_key and settings.langfuse_secret_key):
            return False
        url = urlsplit(settings.langfuse_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            logger.warning("Langfuse endpoint configuration invalid")
            return False
        try:
            root_id = uuid4().hex[:16]
            root = {"traceId": self.trace_id, "spanId": root_id, "name": "talent.workflow", "kind": 1,
                    "startTimeUnixNano": min(span["startTimeUnixNano"] for span in self.spans),
                    "endTimeUnixNano": max(span["endTimeUnixNano"] for span in self.spans)}
            spans = [root, *({"parentSpanId": root_id, **span} for span in self.spans)]
            response = httpx.post(
                settings.langfuse_url.rstrip("/") + "/api/public/otel/v1/traces",
                auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
                headers={"x-langfuse-ingestion-version": "4"},
                json={"resourceSpans": [{"resource": {"attributes": [{"key": "service.name",
                        "value": {"stringValue": "ai-talent-hub"}}]},
                        "scopeSpans": [{"spans": spans}]}]}, timeout=4, follow_redirects=False,
            )
            response.raise_for_status()
            if response.content:
                partial = response.json().get("partialSuccess") or {}
                if int(partial.get("rejectedSpans", 0)):
                    logger.warning("Langfuse rejected spans trace=%s", self.trace_id)
                    return False
            return True
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            logger.warning("Langfuse export failed trace=%s type=%s", self.trace_id, type(exc).__name__)
            return False
