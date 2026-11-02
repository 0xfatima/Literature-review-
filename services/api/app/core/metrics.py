"""Request metrics and Prometheus text rendering (Phase 15). Stdlib only.

Request latency is recorded in-process (one histogram per API replica; Prometheus sums replicas). Job and provider
figures are NOT kept in memory: they are derived from Postgres at scrape time (services/metrics_service.py), so they
cover every worker process and survive restarts. Labels use route TEMPLATES (/api/v1/projects/{project_id}), never
raw paths, so user ids never become label values and cardinality stays bounded.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterable

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
MAX_SERIES = 2000  # hard cap: a bug must not let label cardinality grow without bound


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def labels(**kv: object) -> str:
    return "{" + ",".join(f'{k}="{_esc(str(v))}"' for k, v in kv.items()) + "}" if kv else ""


def num(v: float) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "NaN"
    return repr(float(v)) if isinstance(v, float) else str(v)


class RequestMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._series: dict[tuple[str, str, str], list[float]] = {}  # bucket counts..., count, sum

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        key = (method.upper(), route, f"{status // 100}xx")
        with self._lock:
            row = self._series.get(key)
            if row is None:
                if len(self._series) >= MAX_SERIES:
                    key = (method.upper(), "other", key[2])
                    row = self._series.get(key)
                if row is None:
                    row = [0.0] * (len(BUCKETS) + 2)
                    self._series[key] = row
            for i, b in enumerate(BUCKETS):
                if seconds <= b:
                    row[i] += 1
            row[-2] += 1
            row[-1] += seconds

    def snapshot(self) -> dict[tuple[str, str, str], list[float]]:
        with self._lock:
            return {k: list(v) for k, v in self._series.items()}

    def render(self) -> Iterable[str]:
        name = "srs_http_request_duration_seconds"
        yield f"# HELP {name} API request latency by method, route template and status class (this replica)."
        yield f"# TYPE {name} histogram"
        for (method, route, status), row in sorted(self.snapshot().items()):
            for i, b in enumerate(BUCKETS):
                yield f"{name}_bucket{labels(method=method, route=route, status=status, le=b)} {int(row[i])}"
            yield f"{name}_bucket{labels(method=method, route=route, status=status, le='+Inf')} {int(row[-2])}"
            yield f"{name}_count{labels(method=method, route=route, status=status)} {int(row[-2])}"
            yield f"{name}_sum{labels(method=method, route=route, status=status)} {row[-1]:.6f}"


REQUESTS = RequestMetrics()


def gauge(name: str, help_text: str, rows: Iterable[tuple[dict[str, object], float]], kind: str = "gauge") -> list[str]:
    out = [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
    out += [f"{name}{labels(**lbl)} {num(v)}" for lbl, v in rows]
    return out
