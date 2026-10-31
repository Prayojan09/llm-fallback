"""JSON logging with request and trace correlation, excluding payload fields."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

from opentelemetry import trace

request_id_var: ContextVar[str] = ContextVar("gateway_request_id", default="-")


class JsonFormatter(logging.Formatter):
    """Format only safe standard log fields and correlation identifiers."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a record without exception text or request payloads."""
        context = trace.get_current_span().get_span_context()
        return json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "event": record.getMessage(),
                "request_id": request_id_var.get(),
                "trace_id": f"{context.trace_id:032x}" if context.is_valid else None,
            },
            separators=(",", ":"),
        )


def configure_json_logging(level: str) -> None:
    """Configure a JSON stream handler once without logging payload data."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    if any(getattr(handler, "_gateway_json_handler", False) for handler in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._gateway_json_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
