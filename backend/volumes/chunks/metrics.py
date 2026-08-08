"""Chunk-service signals — half of the Phase 12 gate.

Doc 23 names `chunk_fetch_seconds` as *the* chunk metric, alongside cache and
rejection counters, and sets an SLO of **p95 < 150 ms warm on local SSD**. Row 19
owns Prometheus, Grafana, tracing and dashboards; this module owns the signals,
so row 19 has something to scrape and row 12's gate is demonstrable now.

In-process and deliberately small: a bounded ring of recent durations plus
counters. No new dependency, no scrape endpoint format assumed — a future
exporter can read :func:`snapshot` and render whatever it needs.

Bounded on purpose. An unbounded list of every request duration is a memory leak
with a graph attached; the ring keeps recent behaviour, which is what a p95 over
a live window actually means.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

#: How many recent samples each timing series keeps.
WINDOW = 2048


@dataclass
class _Series:
    """Recent durations in milliseconds, plus a lifetime count."""

    samples: deque = field(default_factory=lambda: deque(maxlen=WINDOW))
    count: int = 0

    def observe(self, ms: float) -> None:
        self.samples.append(ms)
        self.count += 1

    def quantiles(self) -> dict:
        if not self.samples:
            return {"count": self.count, "p50": None, "p95": None, "max": None}
        ordered = sorted(self.samples)
        return {
            "count": self.count,
            "p50": ordered[len(ordered) // 2],
            "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
            "max": ordered[-1],
        }


class ChunkMetrics:
    """Thread-safe collector for the chunk read path."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fetch = _Series()
        self._token_verify = _Series()
        self._bytes_total = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._rejected: dict[str, int] = {}
        self._by_layer: dict[str, int] = {}

    # --- recording -------------------------------------------------------

    def observe_fetch(self, ms: float, *, nbytes: int, layer: str = "image") -> None:
        with self._lock:
            self._fetch.observe(ms)
            self._bytes_total += int(nbytes)
            # A count, not a second timing ring: which layer is being scrubbed
            # is the operational question, and layers are a closed set of two.
            self._by_layer[layer] = self._by_layer.get(layer, 0) + 1

    def observe_token_verify(self, ms: float) -> None:
        with self._lock:
            self._token_verify.observe(ms)

    def cache_hit(self) -> None:
        with self._lock:
            self._cache_hits += 1

    def cache_miss(self) -> None:
        with self._lock:
            self._cache_misses += 1

    def rejected(self, reason: str) -> None:
        """Count a refused request by *reason*, never by message.

        Reasons are a small closed set, so this cannot grow unbounded from
        attacker-controlled input — a counter keyed by free text would be a
        memory leak wearing a metric's name.
        """
        with self._lock:
            self._rejected[reason] = self._rejected.get(reason, 0) + 1

    # --- reading ---------------------------------------------------------

    def snapshot(self) -> dict:
        """A readable point-in-time view. Contains no identifiers or secrets."""
        with self._lock:
            hits, misses = self._cache_hits, self._cache_misses
            total = hits + misses
            return {
                "chunk_fetch_seconds": self._fetch.quantiles(),
                "chunk_token_verify_seconds": self._token_verify.quantiles(),
                "chunk_bytes_total": self._bytes_total,
                "chunk_cache_hits_total": hits,
                "chunk_cache_misses_total": misses,
                "chunk_cache_hit_ratio": (hits / total) if total else None,
                "chunk_rejected_total": dict(self._rejected),
                "chunk_fetch_by_layer_total": dict(self._by_layer),
                "window": WINDOW,
            }

    def reset(self) -> None:
        """Test seam — never called by the serving path."""
        with self._lock:
            self._fetch = _Series()
            self._token_verify = _Series()
            self._bytes_total = 0
            self._cache_hits = 0
            self._cache_misses = 0
            self._rejected = {}
            self._by_layer = {}


#: Process-wide collector. One per process is the correct scope: a future
#: exporter scrapes the process it runs in.
METRICS = ChunkMetrics()


class timed:
    """Context manager recording elapsed milliseconds into a callback."""

    def __init__(self, sink):
        self._sink = sink
        self._start = 0.0
        self.ms = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = (time.perf_counter() - self._start) * 1000.0
        self._sink(self.ms)
        return False
