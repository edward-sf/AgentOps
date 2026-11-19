"""Per-run OTel setup (spec 6.2, section 8).

Each run gets its own `TracerProvider` that collects finished spans in memory. Nothing
touches the global tracer provider, so concurrent runs and tests never share spans, and
no background exporter thread is involved: `records()` is complete once the root ends.
"""

from types import TracebackType

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer

from app.observability.ingest import SpanRecord, from_readable_span

TRACER_NAME = "agentops.runtime"


class RunTracer:
    def __init__(self) -> None:
        self._exporter = InMemorySpanExporter()
        # shutdown_on_exit=False: one provider per run must not pile up atexit hooks.
        self._provider = TracerProvider(shutdown_on_exit=False)
        self._provider.add_span_processor(SimpleSpanProcessor(self._exporter))
        self.tracer: Tracer = self._provider.get_tracer(TRACER_NAME)

    def records(self) -> list[SpanRecord]:
        """The run's finished spans, in the order they ended."""
        return [from_readable_span(span) for span in self._exporter.get_finished_spans()]

    def __enter__(self) -> "RunTracer":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._provider.shutdown()
