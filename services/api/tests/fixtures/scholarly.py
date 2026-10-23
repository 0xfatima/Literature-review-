"""Provider responses for tests, written by hand in each provider's DOCUMENTED response format (not recorded from
the live APIs). Four overlapping works so deduplication has real work to do:

  W1  OpenAlex + Europe PMC (MED:31111111)   same DOI and PMID   -> one paper, matched by DOI
  W2  OpenAlex + Crossref                    same DOI            -> one paper (additional tier)
  W3  OpenAlex (arXiv DOI) + arXiv 2101.00001                    -> one paper, matched by arXiv id
  E2  Europe PMC only (MED:32222222)                             -> new paper, open PDF link
  X2  Crossref only (CC licence)                                 -> new paper (additional tier)

Open-access-first run (OpenAlex + Europe PMC + arXiv): 6 records, 4 unique papers, 2 duplicates.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

DOI_W1, DOI_W2, DOI_X2 = "10.1016/j.jad.2020.01.001", "10.1001/jama.2019.12345", "10.5555/open.2021.7"
T_W1 = "Aerobic exercise for depression in older adults: a randomised controlled trial"
T_W2 = "Walking programmes and depressive symptoms in later life"
T_W3 = "Machine learning prediction of exercise response in depression"
T_E2 = "Running therapy versus usual care for late-life depression"
T_X2 = "Community exercise and mood in older people: a cohort study"


def openalex_work(
    wid: str,
    title: str,
    doi: str | None,
    *,
    pmid: str | None = None,
    oa: bool = False,
    pdf: str | None = None,
    wtype: str = "article",
    authors: tuple[str, ...] = ("Ann Smith",),
    year: int = 2020,
) -> dict[str, Any]:
    return {
        "id": f"https://openalex.org/{wid}",
        "doi": f"https://doi.org/{doi}" if doi else None,
        "title": title,
        "display_name": title,
        "publication_year": year,
        "ids": {
            "openalex": f"https://openalex.org/{wid}",
            **({"pmid": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}"} if pmid else {}),
        },
        "language": "en",
        "type": wtype,
        "primary_location": {
            "landing_page_url": f"https://example.org/{wid}",
            "source": {"display_name": "Journal of Tests", "type": "journal"},
        },
        "best_oa_location": {"pdf_url": pdf, "landing_page_url": f"https://repo.example.org/{wid}", "license": "cc-by"}
        if oa
        else None,
        "open_access": {
            "is_oa": oa,
            "oa_status": "green" if oa else "closed",
            "oa_url": f"https://repo.example.org/{wid}" if oa else None,
        },
        "authorships": [{"author": {"display_name": a}} for a in authors],
        "abstract_inverted_index": {"Older": [0], "adults": [1], "with": [2], "depression": [3], "exercised.": [4]},
        "keywords": [{"display_name": "depression"}],
        "is_retracted": False,
    }


OPENALEX_PAGE_1 = {
    "meta": {"count": 3, "next_cursor": "cursor-2"},
    "results": [
        openalex_work("W1", T_W1, DOI_W1, pmid="31111111", oa=True, pdf="https://repo.example.org/W1.pdf"),
        openalex_work("W2", T_W2, DOI_W2, authors=("Bo Chen",), year=2019),
    ],
}
OPENALEX_PAGE_2 = {
    "meta": {"count": 3, "next_cursor": None},
    "results": [
        openalex_work("W3", T_W3, "10.48550/arXiv.2101.00001", wtype="preprint", authors=("Cara Diaz",), year=2021)
    ],
}

EUROPEPMC = {
    "hitCount": 2,
    "nextCursorMark": "*",  # same as the request's cursor: no further page
    "resultList": {
        "result": [
            {
                "id": "31111111",
                "source": "MED",
                "pmid": "31111111",
                "doi": DOI_W1.upper(),
                "title": T_W1 + ".",
                "authorList": {"author": [{"fullName": "Smith A", "lastName": "Smith"}]},
                "pubYear": "2020",
                "abstractText": "<h4>Background</h4>Older adults with depression exercised.",
                "language": "eng",
                "pubTypeList": {"pubType": ["Randomized Controlled Trial", "Journal Article"]},
                "isOpenAccess": "N",
                "journalInfo": {"journal": {"title": "Journal of Tests"}},
            },
            {
                "id": "32222222",
                "source": "MED",
                "pmid": "32222222",
                "title": T_E2,
                "authorList": {"author": [{"fullName": "Evans R", "lastName": "Evans"}]},
                "pubYear": "2022",
                "abstractText": "We compared running therapy with usual care in adults over 60.",
                "language": "eng",
                "isOpenAccess": "Y",
                "pmcid": "PMC9000001",
                "fullTextUrlList": {
                    "fullTextUrl": [
                        {
                            "availabilityCode": "OA",
                            "documentStyle": "pdf",
                            "url": "https://europepmc.org/articles/PMC9000001?pdf=render",
                        }
                    ]
                },
            },
        ]
    },
}

ARXIV_ATOM = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2101.00001v2</id>
    <published>2021-01-04T00:00:00Z</published>
    <title>{T_W3}</title>
    <summary>We predict who benefits from exercise for depression.</summary>
    <author><name>Cara Diaz</name></author>
    <link href="http://arxiv.org/abs/2101.00001v2" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2101.00001v2" rel="related" type="application/pdf"/>
    <arxiv:primary_category term="cs.LG"/>
    <category term="cs.LG"/>
  </entry>
</feed>"""

CROSSREF = {
    "status": "ok",
    "message": {
        "total-results": 2,
        "next-cursor": None,
        "items": [
            {
                "DOI": DOI_W2,
                "title": [T_W2],
                "author": [{"given": "Bo", "family": "Chen"}],
                "type": "journal-article",
                "issued": {"date-parts": [[2019, 5]]},
                "container-title": ["JAMA"],
            },
            {
                "DOI": DOI_X2,
                "title": [T_X2],
                "author": [{"given": "Dee", "family": "Fox"}],
                "type": "journal-article",
                "issued": {"date-parts": [[2021]]},
                "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
                "link": [{"URL": "https://publisher.example.org/x2.pdf", "content-type": "application/pdf"}],
            },
        ],
    },
}


class ScholarlyTransport:
    """httpx.MockTransport routing by host, with failure injection and a request log.

    fail:        {host: status}               every request to host answers with that status
    fail_after:  {host: n}                    the first n requests succeed, the rest answer 503
    """

    def __init__(self, *, fail: dict[str, int] | None = None, fail_after: dict[str, int] | None = None):
        self.fail, self.fail_after = fail or {}, fail_after or {}
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def count(self, host: str) -> int:
        return sum(1 for r in self.requests if r.url.host == host)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host in self.fail:
            return httpx.Response(self.fail[host], json={"error": "injected"})
        if host in self.fail_after and self.count(host) > self.fail_after[host]:
            return httpx.Response(503, json={"error": "injected"})
        params = request.url.params
        if host == "api.openalex.org":
            return httpx.Response(200, json=OPENALEX_PAGE_2 if params.get("cursor") == "cursor-2" else OPENALEX_PAGE_1)
        if host == "www.ebi.ac.uk":
            return httpx.Response(200, json=EUROPEPMC)
        if host == "export.arxiv.org":
            return httpx.Response(200, text=ARXIV_ATOM)
        if host == "api.crossref.org":
            return httpx.Response(200, json=CROSSREF)
        return httpx.Response(404)


def json_response(body: Any, status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _r: httpx.Response(
        status, content=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )
