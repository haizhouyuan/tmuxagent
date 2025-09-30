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
ORCHESTRATOR_JSON_PARSE_FAILURES = Counter(
    "orchestrator_json_parse_failures_total",
    "Codex JSON parse failures grouped by branch and error kind",
    labelnames=("branch", "kind"),
)
ORCHESTRATOR_UTF8_DECODE_ERRORS = Counter(
    "orchestrator_utf8_decode_errors_total",
    "UTF-8 decode/encoding issues encountered while running Codex",
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
ORCHESTRATOR_COMMAND_FAILURES = Counter(
    "orchestrator_command_failures_total",
    "Number of orchestrator-injected commands that exited non-zero",
    labelnames=("branch",),
)
ORCHESTRATOR_COMMAND_SUCCESSES = Counter(
    "orchestrator_command_success_total",
    "Number of orchestrator-injected commands that exited with code 0",
    labelnames=("branch",),
)


def record_command(result: str) -> None:
    """Increment the orchestrator command counter."""

    ORCHESTRATOR_COMMANDS_TOTAL.labels(result=result).inc()


def record_error(branch: str) -> None:
    """Track orchestrator decision errors for alerting."""

    ORCHESTRATOR_DECISION_ERRORS_TOTAL.labels(branch=branch).inc()


def record_json_parse_failure(branch: str, kind: str) -> None:
    """Track Codex JSON解析失败，方便排查错误类型。"""

    ORCHESTRATOR_JSON_PARSE_FAILURES.labels(branch=branch, kind=kind).inc()


def record_utf8_decode_error(branch: str) -> None:
    """针对 UTF-8 解码失败记录指标。"""

    ORCHESTRATOR_UTF8_DECODE_ERRORS.labels(branch=branch).inc()


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


def record_command_failure(branch: str) -> None:
    """Increment failure counter for orchestrator commands."""

    ORCHESTRATOR_COMMAND_FAILURES.labels(branch=branch).inc()


def record_command_success(branch: str) -> None:
    """Increment success counter for orchestrator commands."""

    ORCHESTRATOR_COMMAND_SUCCESSES.labels(branch=branch).inc()
