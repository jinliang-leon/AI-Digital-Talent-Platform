"""Send a synthetic trace and report collector acknowledgement without printing credentials."""
import argparse
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.core.config import Settings
from backend.app.core.tracing import Trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    settings = Settings.load()
    if not (settings.langfuse_url and settings.langfuse_public_key and settings.langfuse_secret_key):
        parser.error("Set TALENT_LANGFUSE_URL, TALENT_LANGFUSE_PUBLIC_KEY and TALENT_LANGFUSE_SECRET_KEY locally")
    trace = Trace(settings, uuid4().hex)
    with trace.step("synthetic.acceptance"):
        with trace.step("knowledge.retrieve", hits=1):
            pass
        with trace.step("chat.tool", intent="knowledge_answer"):
            pass
    if not trace.flush():
        print(f"FAIL Langfuse collector did not acknowledge synthetic trace {trace.trace_id}")
        return 1
    print(f"PASS Langfuse collector acknowledged synthetic trace {trace.trace_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
