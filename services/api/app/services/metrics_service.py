"""Cross-process job, queue and provider metrics derived from Postgres (Phase 15).

Every figure is computed from persisted rows over a trailing window (METRICS_WINDOW_S), so it is the same no matter
which replica is scraped and it includes work done by every worker process. Aggregates only: no project, user or
paper identifiers are exposed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.metrics import gauge


def _rows(db: Session, sql: str, **params: Any) -> list[Any]:
    return list(db.execute(text(sql), params).all())


def render(db: Session, window_s: int) -> list[str]:
    since = datetime.now(UTC) - timedelta(seconds=max(60, window_s))
    w = f"over the last {max(60, window_s)} s"
    out: list[str] = []

    state = _rows(
        db,
        """SELECT type,
                  CASE WHEN status = 'running' THEN 'running'
                       WHEN enqueued_at IS NULL THEN 'waiting_for_capacity' ELSE 'admitted' END AS state,
                  count(*)
           FROM jobs WHERE status IN ('queued', 'running') GROUP BY 1, 2""",
    )
    out += gauge(
        "srs_jobs_active",
        "Active jobs by type and state (waiting_for_capacity = held back by fair-share limits).",
        [({"type": t, "state": s}, n) for t, s, n in state],
    )
    oldest = _rows(
        db,
        """SELECT type, extract(epoch FROM now() - min(created_at)) FROM jobs
           WHERE status = 'queued' GROUP BY 1""",
    )
    out += gauge(
        "srs_jobs_oldest_queued_age_seconds",
        "Age of the oldest queued job by type (backlog indicator).",
        [({"type": t}, float(a)) for t, a in oldest],
    )
    finished = _rows(
        db,
        """SELECT type, status, count(*) FROM jobs
           WHERE finished_at >= :since GROUP BY 1, 2""",
        since=since,
    )
    out += gauge(
        "srs_jobs_finished",
        f"Jobs finished {w}, by type and final status.",
        [({"type": t, "status": s}, n) for t, s, n in finished],
    )
    totals: dict[str, list[int]] = {}
    for t, s, n in finished:
        tot = totals.setdefault(t, [0, 0])
        tot[0] += n
        tot[1] += n if s == "failed" else 0
    out += gauge(
        "srs_job_failure_ratio",
        f"Share of jobs that failed {w}, by type.",
        [({"type": t}, f / n) for t, (n, f) in sorted(totals.items()) if n],
    )
    q = _rows(
        db,
        """SELECT type,
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM started_at - created_at)),
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM started_at - created_at)),
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - started_at)),
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - started_at))
           FROM jobs WHERE finished_at >= :since AND started_at IS NOT NULL GROUP BY 1""",
        since=since,
    )
    out += gauge(
        "srs_job_queue_delay_seconds",
        f"Time from submission to start (includes fair-share waiting) {w}, by type and quantile.",
        [({"type": t, "quantile": qn}, float(v)) for t, a, b, _, _ in q for qn, v in (("0.5", a), ("0.95", b))],
    )
    out += gauge(
        "srs_job_duration_seconds",
        f"Run time of finished jobs {w}, by type and quantile.",
        [({"type": t, "quantile": qn}, float(v)) for t, _, _, c, d in q for qn, v in (("0.5", c), ("0.95", d))],
    )
    llm = _rows(
        db,
        """SELECT provider, model, status, count(*),
                  coalesce(sum(coalesce(input_tokens, 0) + coalesce(output_tokens, 0)), 0)
           FROM model_runs WHERE created_at >= :since GROUP BY 1, 2, 3""",
        since=since,
    )
    out += gauge(
        "srs_llm_calls",
        f"AI provider calls {w}, by provider, model and status.",
        [({"provider": p, "model": m, "status": s}, n) for p, m, s, n, _ in llm],
    )
    out += gauge(
        "srs_llm_tokens",
        f"AI tokens (input + output) {w}, by provider and model.",
        [({"provider": p, "model": m}, tok) for p, m, s, _, tok in llm if s == "succeeded"],
    )
    day = _rows(
        db,
        """SELECT coalesce(sum(coalesce(input_tokens, 0) + coalesce(output_tokens, 0)), 0) FROM model_runs
           WHERE created_at >= now() - interval '24 hours'""",
    )
    out += gauge(
        "srs_llm_tokens_24h",
        "AI tokens over the last 24 h, all projects (compare with LLM_GLOBAL_DAILY_TOKEN_BUDGET).",
        [({}, day[0][0] if day else 0)],
    )
    scholarly = _rows(
        db,
        """SELECT provider, coalesce(sum(request_count), 0), count(*) FILTER (WHERE error_kind = 'rate_limited')
           FROM search_queries WHERE coalesce(finished_at, retrieved_at, started_at) >= :since GROUP BY 1""",
        since=since,
    )
    out += gauge(
        "srs_scholarly_requests",
        f"HTTP requests made to scholarly providers {w}, by provider.",
        [({"provider": p}, n) for p, n, _ in scholarly],
    )
    out += gauge(
        "srs_scholarly_rate_limited_queries",
        f"Search queries that ended rate-limited {w}, by provider.",
        [({"provider": p}, r) for p, _, r in scholarly],
    )
    return out
