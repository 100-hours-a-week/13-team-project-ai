import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor


def setup_tracing(app):
    """OTLP_ENDPOINT 환경변수가 설정된 경우에만 트레이싱 활성화"""
    otlp_endpoint = os.getenv("OTLP_ENDPOINT")
    if not otlp_endpoint:
        return

    resource = Resource.create({
        "service.name": os.getenv("OTEL_SERVICE_NAME", "recommend"),
        "service.namespace": "moyeobab",
    })

    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
