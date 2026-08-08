# Production observability

JoyhouseBot keeps the replayable execution trace in PostgreSQL and exports a
second, operational view through Prometheus and OpenTelemetry.

## Runtime configuration

Install the optional telemetry dependencies and configure each API/Worker
process:

```bash
pip install 'joyhousebot[observability]'
export JOYHOUSEBOT_OTEL_ENABLED=true
export JOYHOUSEBOT_ENVIRONMENT=production
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_PROPAGATORS=tracecontext,baggage
```

When telemetry is enabled but the optional dependencies are absent, startup
fails instead of silently running without traces. API, Agent Worker,
Scheduler and Channel Worker use distinct `service.name` values. W3C
`traceparent`/`tracestate` are frozen into Run options so execution in another
process continues the submitting request trace.

`/metrics` remains fail-closed and requires `JOYHOUSEBOT_METRICS_TOKEN`. Use
`ops/prometheus/prometheus.yml`, `ops/prometheus/joyhousebot-alerts.yml` and
`ops/grafana/joyhousebot-overview.json` as deployment inputs.

## Data policy

OTLP attributes contain identifiers, status, timing and bounded error types;
Prometheus labels never contain `user_id`, Prompt, Artifact content, tokens or
arbitrary Capability input. Full request/response bodies remain in the
permission-controlled PostgreSQL Trace Blob store and must not be exported to
Prometheus or ordinary logs.
