"""Prometheus metrics helpers for tmux-agent components."""
from __future__ import annotations

try:
    from prometheus_client import Counter
    from prometheus_client import Gauge
    from prometheus_client import Histogram
    from prometheus_client import start_http_server as _start_http_server
except ImportError:  # pragma: no cover - optional dependency fallback
    class _NoopMetric:
        def labels(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return self

        def inc(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            return

        def set(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            return

        def observe(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            return

        def remove(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            return

    def _noop_metric(*_args, **_kwargs) -> _NoopMetric:  # type: ignore[no-untyped-def]
        return _NoopMetric()

    Counter = Gauge = Histogram = _noop_metric  # type: ignore[assignment]
    _start_http_server = None  # type: ignore[assignment]

ORCHESTRATOR_COMMANDS_TOTAL = Counter(
    "orchestrator_commands_total",
    "Total number of orchestrator commands processed",
    labelnames=("result",),
)
ORCHESTRATOR_DECISION_ERRORS_TOTAL = Counter(
    "orchestrator_decision_errors_total",
    "Number of orchestrator decision failures",
    labelnames=("branch",),
)
ORCHESTRATOR_QUEUE_SIZE = Gauge(
    "orchestrator_queue_size",
    "Current queued command count per branch",
    labelnames=("branch",),
)
ORCHESTRATOR_PENDING_CONFIRMATIONS = Gauge(
    "orchestrator_pending_confirmation_total",
    "Pending manual confirmations per branch",
    labelnames=("branch",),
)
ORCHESTRATOR_DECISION_LATENCY = Histogram(
    "orchestrator_decision_latency_seconds",
    "Time taken to evaluate a single orchestrator decision",
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60),
)


def record_command(result: str) -> None:
    """Increment the orchestrator command counter."""

    ORCHESTRATOR_COMMANDS_TOTAL.labels(result=result).inc()


def record_error(branch: str) -> None:
    """Track orchestrator decision errors for alerting."""

    ORCHESTRATOR_DECISION_ERRORS_TOTAL.labels(branch=branch).inc()


def set_queue_size(branch: str, size: int) -> None:
    """Update queued command gauge for the branch."""

    if size <= 0:
        try:
            ORCHESTRATOR_QUEUE_SIZE.remove(branch)
        except KeyError:
            pass
        return
    ORCHESTRATOR_QUEUE_SIZE.labels(branch=branch).set(size)


def set_pending_confirmations(branch: str, size: int) -> None:
    """Update gauge tracking pending confirmations for the branch."""

    if size <= 0:
        try:
            ORCHESTRATOR_PENDING_CONFIRMATIONS.remove(branch)
        except KeyError:
            pass
        return
    ORCHESTRATOR_PENDING_CONFIRMATIONS.labels(branch=branch).set(size)


def observe_decision_latency(duration_seconds: float) -> None:
    """Observe decision runtime."""

    ORCHESTRATOR_DECISION_LATENCY.observe(duration_seconds)


def start_server(port: int, host: str = "0.0.0.0") -> None:
    """Start Prometheus metrics HTTP server if client is available."""

    if _start_http_server is None:  # pragma: no cover - optional dependency missing
        return
    _start_http_server(port, addr=host)
