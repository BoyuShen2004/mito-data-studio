"""Low-cardinality operational signals for liveness, readiness and scraping.

The collector is deliberately dependency-free and process-local. Gunicorn can
later be pointed at a multiprocess Prometheus collector without changing the
health contract or request instrumentation introduced here.
"""

from __future__ import annotations

import hmac
import os
import threading
import time
import uuid
from collections import Counter, defaultdict, deque
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET


_WINDOW = 2048


class RequestMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter[tuple[str, str]] = Counter()
        self._durations: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=_WINDOW)
        )

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        status_class = f"{status // 100}xx"
        with self._lock:
            self._counts[(method, status_class)] += 1
            self._durations[route].append(seconds)

    def render(self) -> list[str]:
        lines = [
            "# HELP mito_http_requests_total HTTP responses by method and status class.",
            "# TYPE mito_http_requests_total counter",
        ]
        with self._lock:
            counts = dict(self._counts)
            durations = {route: list(values) for route, values in self._durations.items()}
        for (method, status_class), count in sorted(counts.items()):
            lines.append(
                f'mito_http_requests_total{{method="{method}",status="{status_class}"}} {count}'
            )
        lines += [
            "# HELP mito_http_request_duration_seconds Recent request duration summary.",
            "# TYPE mito_http_request_duration_seconds summary",
        ]
        for route, values in sorted(durations.items()):
            if not values:
                continue
            ordered = sorted(values)
            p50 = ordered[len(ordered) // 2]
            p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
            safe_route = route.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(
                f'mito_http_request_duration_seconds{{route="{safe_route}",quantile="0.5"}} {p50:.6f}'
            )
            lines.append(
                f'mito_http_request_duration_seconds{{route="{safe_route}",quantile="0.95"}} {p95:.6f}'
            )
        return lines

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._durations.clear()


REQUEST_METRICS = RequestMetrics()


class RequestObservabilityMiddleware:
    """Attach a request id and record bounded, route-pattern metrics."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = uuid.uuid4().hex
        started = time.perf_counter()
        try:
            response = self.get_response(request)
        finally:
            elapsed = time.perf_counter() - started
        route = getattr(getattr(request, "resolver_match", None), "route", None)
        route = route or "unmatched"
        REQUEST_METRICS.observe(request.method, route, response.status_code, elapsed)
        response["X-Request-ID"] = request.request_id
        return response


@require_GET
def healthz(_request: HttpRequest) -> JsonResponse:
    """Process liveness only; dependency failures belong to readiness."""
    return JsonResponse({"status": "alive"})


def _ready_checks() -> dict[str, bool]:
    checks: dict[str, bool] = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            checks["database"] = cursor.fetchone() == (1,)
    except Exception:
        checks["database"] = False

    root = Path(settings.MITO_DATA_ROOT)
    checks["data_root"] = root.is_dir() and os.access(root, os.W_OK)
    try:
        free = os.statvfs(root).f_bavail * os.statvfs(root).f_frsize
        checks["disk"] = free >= settings.MITO_READY_MIN_FREE_BYTES
    except OSError:
        checks["disk"] = False
    return checks


@require_GET
def readyz(_request: HttpRequest) -> JsonResponse:
    checks = _ready_checks()
    ready = all(checks.values())
    return JsonResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status=200 if ready else 503,
    )


def _metrics_authorized(request: HttpRequest) -> bool:
    expected = settings.MITO_METRICS_BEARER_TOKEN
    if not expected:
        return False
    supplied = request.headers.get("Authorization", "")
    if not supplied.startswith("Bearer "):
        return False
    return hmac.compare_digest(supplied[7:], expected)


@require_GET
def metrics(request: HttpRequest) -> HttpResponse:
    if not _metrics_authorized(request):
        # 404 avoids advertising an operational endpoint when scraping is not
        # configured, while still providing a conventional path to operators.
        return HttpResponse(status=404)

    from core.choices import ACTIVE_JOB_STATUSES, ProcessingJobStatus
    from django.db.models import Count
    from processing.models import ProcessingJob
    from volumes.chunks.metrics import METRICS as CHUNK_METRICS

    lines = REQUEST_METRICS.render()
    job_counts = {
        row["status"]: row["count"]
        for row in ProcessingJob.objects.values("status").annotate(count=Count("id"))
    }
    lines += [
        "# HELP mito_worker_queue_depth Processing jobs waiting to be dispatched.",
        "# TYPE mito_worker_queue_depth gauge",
        f'mito_worker_queue_depth {job_counts.get(ProcessingJobStatus.QUEUED, 0)}',
        "# HELP mito_worker_active_jobs Processing jobs submitted or running.",
        "# TYPE mito_worker_active_jobs gauge",
        f'mito_worker_active_jobs {sum(job_counts.get(s, 0) for s in ACTIVE_JOB_STATUSES)}',
    ]
    chunk = CHUNK_METRICS.snapshot()
    lines += [
        "# HELP mito_chunk_bytes_total Bytes served by the chunk path.",
        "# TYPE mito_chunk_bytes_total counter",
        f"mito_chunk_bytes_total {chunk['chunk_bytes_total']}",
        "# HELP mito_chunk_cache_hits_total Chunk cache hits.",
        "# TYPE mito_chunk_cache_hits_total counter",
        f"mito_chunk_cache_hits_total {chunk['chunk_cache_hits_total']}",
        "# HELP mito_chunk_cache_misses_total Chunk cache misses.",
        "# TYPE mito_chunk_cache_misses_total counter",
        f"mito_chunk_cache_misses_total {chunk['chunk_cache_misses_total']}",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; version=0.0.4")
