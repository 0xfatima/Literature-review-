"""Local mock scholarly + AI provider for load tests (Phase 15). NOT used by the application at runtime.

Speaks the documented response formats of OpenAlex (/works, cursor paging), Europe PMC (/search, cursorMark) and
Gemini (models/{model}:generateContent), so the REAL adapters, HTTP client, retries, Redis throttles and semaphores
run unchanged; the application reaches it through SCHOLARLY_ENDPOINTS and LLM_BASE_URL.

It records what a real provider would see: requests per provider, 429s served, the PEAK number of concurrent requests
and the peak requests in any 1-second window. Those numbers prove (or disprove) that the app's shared limits hold.
Records are synthetic and labelled as such in their titles; nothing here pretends to be real literature.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

TOPICS = ["exercise", "depression", "older adults", "cohort", "randomised", "walking", "mood", "therapy"]


class Stats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: dict[str, int] = defaultdict(int)
        self.rate_limited: dict[str, int] = defaultdict(int)
        self.inflight: dict[str, int] = defaultdict(int)
        self.peak_inflight: dict[str, int] = defaultdict(int)
        self.times: dict[str, deque[float]] = defaultdict(deque)
        self.peak_per_s: dict[str, int] = defaultdict(int)
        self.tokens = 0

    def enter(self, kind: str) -> None:
        now = time.monotonic()
        with self.lock:
            self.requests[kind] += 1
            self.inflight[kind] += 1
            self.peak_inflight[kind] = max(self.peak_inflight[kind], self.inflight[kind])
            q = self.times[kind]
            q.append(now)
            while q and now - q[0] > 1.0:
                q.popleft()
            self.peak_per_s[kind] = max(self.peak_per_s[kind], len(q))

    def leave(self, kind: str) -> None:
        with self.lock:
            self.inflight[kind] -= 1

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "requests": dict(self.requests),
                "rate_limited_responses": dict(self.rate_limited),
                "peak_concurrent_requests": dict(self.peak_inflight),
                "peak_requests_per_second": dict(self.peak_per_s),
                "tokens_served": self.tokens,
            }


def _openalex_work(i: int) -> dict[str, Any]:
    doi = f"10.5555/synthetic.{i}"
    title = f"Synthetic load-test record {i}: {TOPICS[i % 8]} and {TOPICS[(i * 3) % 8]}"
    words = (f"This synthetic abstract {i} describes {TOPICS[i % 8]} outcomes in a {TOPICS[(i + 2) % 8]} sample").split()
    return {
        "id": f"https://openalex.org/W{900000000 + i}",
        "doi": f"https://doi.org/{doi}",
        "title": title,
        "display_name": title,
        "publication_year": 2000 + i % 25,
        "ids": {"openalex": f"https://openalex.org/W{900000000 + i}"},
        "language": "en",
        "type": "article",
        "primary_location": {"landing_page_url": f"https://example.org/w{i}", "source": {"display_name": "Synthetic"}},
        "best_oa_location": None,
        "open_access": {"is_oa": False, "oa_status": "closed", "oa_url": None},
        "authorships": [{"author": {"display_name": f"Author{i % 97} Lastname{i % 89}"}}],
        "abstract_inverted_index": {w: [k] for k, w in enumerate(words)},
        "keywords": [{"display_name": TOPICS[i % 8]}],
        "is_retracted": False,
    }


def _epmc(i: int) -> dict[str, Any]:
    return {
        "id": str(40000000 + i),
        "source": "MED",
        "pmid": str(40000000 + i),
        "doi": f"10.5555/synthetic.{i}",
        "title": f"Synthetic load-test record {i}: {TOPICS[i % 8]} and {TOPICS[(i * 3) % 8]}",
        "authorList": {"author": [{"fullName": f"Lastname{i % 89} A", "lastName": f"Lastname{i % 89}"}]},
        "pubYear": str(2000 + i % 25),
        "abstractText": f"Synthetic abstract {i}.",
        "language": "eng",
        "isOpenAccess": "N",
        "journalInfo": {"journal": {"title": "Synthetic"}},
    }


class MockConfig:
    def __init__(
        self,
        *,
        openalex_records: int = 2000,
        epmc_records: int = 1500,
        overlap: int = 500,
        scholarly_latency_s: float = 0.05,
        llm_latency_s: float = 0.4,
        rate_limit_every: int = 0,
    ):
        self.openalex_records, self.epmc_records, self.overlap = openalex_records, epmc_records, overlap
        self.scholarly_latency_s, self.llm_latency_s, self.rate_limit_every = (
            scholarly_latency_s,
            llm_latency_s,
            rate_limit_every,
        )


def _answer_fields(prompt: str) -> list[dict[str, Any]]:
    """Answer only from the fenced page text: a value with a quote that really occurs on the page, or not_found."""
    pages = re.findall(r"=== PAGE (\d+) ===\n<<<UNTRUSTED [^\n]*>>>\n(.*?)\n<<<END UNTRUSTED>>>", prompt, re.S)
    keys = re.findall(r"^- ([a-z0-9_]+):", prompt, re.M)
    out: list[dict[str, Any]] = []
    for key in keys:
        hit = None
        for page_no, body in pages:
            m = re.search(r"enrolled (\d+) adult participants", body)
            if key == "n" and m:
                hit = {"status": "found", "value": m.group(1), "page": int(page_no), "quote": m.group(0)}
            elif key == "design" and "RCT" in body:
                hit = {"status": "found", "value": "RCT", "page": int(page_no), "quote": "This RCT enrolled"}
            if hit:
                break
        out.append({"field_key": key, **(hit or {"status": "not_found"})})
    return out


def make_handler(cfg: MockConfig, stats: Stats) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_: Any) -> None:  # quiet
            return

        def _send(self, code: int, body: Any, headers: dict[str, str] | None = None) -> None:
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _maybe_429(self, kind: str) -> bool:
            if cfg.rate_limit_every and stats.requests[kind] % cfg.rate_limit_every == 0:
                with stats.lock:
                    stats.rate_limited[kind] += 1
                self._send(429, {"error": "rate limited (mock)"}, {"Retry-After": "1"})
                return True
            return False

        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            kind = "openalex" if u.path.startswith("/openalex") else "europepmc"
            stats.enter(kind)
            try:
                time.sleep(cfg.scholarly_latency_s * random.uniform(0.5, 1.5))  # noqa: S311
                if self._maybe_429(kind):
                    return
                if kind == "openalex":
                    size = int(q.get("per-page", 100))
                    start = 0 if q.get("cursor", "*") == "*" else int(q["cursor"])
                    ids = range(start, min(start + size, cfg.openalex_records))
                    nxt = start + size if start + size < cfg.openalex_records else None
                    body = {"meta": {"count": cfg.openalex_records, "next_cursor": str(nxt) if nxt else None},
                            "results": [_openalex_work(i) for i in ids]}
                else:
                    size = int(q.get("pageSize", 100))
                    cur = q.get("cursorMark", "*")
                    start = 0 if cur == "*" else int(cur)
                    first = cfg.openalex_records - cfg.overlap  # the last `overlap` OpenAlex works also appear here
                    ids = range(first + start, min(first + start + size, first + cfg.epmc_records))
                    nxt = start + size if start + size < cfg.epmc_records else start
                    body = {"hitCount": cfg.epmc_records, "nextCursorMark": str(nxt) if nxt != start else cur,
                            "resultList": {"result": [_epmc(i) for i in ids]}}
                self._send(200, body)
            finally:
                stats.leave(kind)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length) or b"{}")
            stats.enter("gemini")
            try:
                time.sleep(cfg.llm_latency_s * random.uniform(0.7, 1.3))  # noqa: S311
                if self._maybe_429("gemini"):
                    return
                prompt = "".join(p.get("text", "") for c in req.get("contents", []) for p in c.get("parts", []))
                model = self.path.split("/models/")[-1].split(":")[0]
                text = json.dumps({"fields": _answer_fields(prompt)})
                tin, tout = max(1, len(prompt) // 4), max(1, len(text) // 4)
                with stats.lock:
                    stats.tokens += tin + tout
                self._send(200, {
                    "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": tin, "candidatesTokenCount": tout},
                    "modelVersion": model,
                })
            finally:
                stats.leave("gemini")

    return Handler


def start(cfg: MockConfig, port: int = 0) -> tuple[ThreadingHTTPServer, Stats]:
    stats = Stats()
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg, stats))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, stats
