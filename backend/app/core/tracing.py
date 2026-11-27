"""Emit allowlisted workflow metadata to Langfuse over OTLP/HTTP."""
from contextlib import contextmanager
import logging
from time import time_ns
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)


class Trace:
    def __init__(self, settings, trace_id):
        self.settings = settings
        self.trace_id = trace_id
        self.spans = []

    @contextmanager
    def step(self, name, **metadata):
        started = time_ns()
        status = 1
        try:
            yield metadata
        except Exception:
            status = 2
            raise
        finally:
            self.spans.append({
                "traceId": self.trace_id, "spanId": uuid4().hex[:16], "name": name,
                "kind": 1, "startTimeUnixNano": str(started), "endTimeUnixNano": str(time_ns()),
                "status": {"code": status},
                "attributes": [
                    {"key": "talent." + key, "value": {"intValue" if type(value) is int else "stringValue": str(value)}}
                    for key, value in metadata.items() if value is not None
                ],
            })

    def flush(self):
        settings = self.settings
        if not self.spans or not (settings.langfuse_url and settings.langfuse_public_key and settings.langfuse_secret_key):
            return
        url = urlsplit(settings.langfuse_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            logger.warning("Langfuse endpoint configuration invalid")
            return
        try:
            root_id = uuid4().hex[:16]
            root = {"traceId": self.trace_id, "spanId": root_id, "name": "talent.workflow", "kind": 1,
                    "startTimeUnixNano": min(span["startTimeUnixNano"] for span in self.spans),
                    "endTimeUnixNano": max(span["endTimeUnixNano"] for span in self.spans)}
            spans = [root, *(dict(span, parentSpanId=root_id) for span in self.spans)]
            response = httpx.post(
                settings.langfuse_url.rstrip("/") + "/api/public/otel/v1/traces",
                auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
                headers={"x-langfuse-ingestion-version": "4"},
                json={"resourceSpans": [{"resource": {"attributes": [{"key": "service.name",
                        "value": {"stringValue": "ai-talent-hub"}}]},
                        "scopeSpans": [{"spans": spans}]}]}, timeout=4, follow_redirects=False,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Langfuse export failed trace=%s type=%s", self.trace_id, type(exc).__name__)
