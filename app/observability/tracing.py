"""OpenTelemetry SDK setup and shared tracer access."""

from threading import Lock

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.trace import Tracer

_lock = Lock()
_configured = False


def configure_tracing(service_name: str, endpoint: str | None, sample_ratio: float) -> None:
    """Install one process-wide SDK provider and optional OTLP exporter."""
    global _configured
    with _lock:
        if _configured:
            return
        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name}),
            sampler=TraceIdRatioBased(sample_ratio),
        )
        if endpoint:
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
            )
        trace.set_tracer_provider(provider)
        _configured = True


def get_tracer() -> Tracer:
    """Return the gateway tracer for nested request operations."""
    return trace.get_tracer("llm_gateway")
