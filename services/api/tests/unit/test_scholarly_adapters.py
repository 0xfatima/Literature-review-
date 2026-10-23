"""Provider adapters against documented response shapes (tests/fixtures/scholarly.py), through the real HTTP layer
with a mock transport: paging, normalisation, open-access fields, malformed bodies, retries and errors."""

import httpx
import pytest

from app.providers.scholarly import match_protocol_sources, registry
from app.providers.scholarly.arxiv import ArxivSource
from app.providers.scholarly.crossref import CrossrefSource
from app.providers.scholarly.europepmc import EuropePMCSource
from app.providers.scholarly.http import ProviderError, public_url
from app.providers.scholarly.openalex import OpenAlexSource, rebuild_abstract
from app.search import query as q
from app.search.derive import FilterPlan
from app.search.records import RecordError
from tests.fakes import no_wait_fetcher
from tests.fixtures import scholarly as fx

EXPR = q.parse("exercise AND depression")
NF = FilterPlan(None, None, [], [], [])


def fetch(source, transport, cursor=None, size=50):
    http = no_wait_fetcher(transport)
    return source.fetch_page(http, source.build_query(EXPR, NF), cursor, size), http


def test_openalex_pages_with_the_cursor_and_sends_contact_details_only_at_request_time():
    t = fx.ScholarlyTransport()
    src = OpenAlexSource(mailto="me@example.org", api_key="k-123")
    p1, _ = fetch(src, t.transport)
    assert len(p1.records) == 2 and p1.next_cursor == "cursor-2" and p1.total == 3
    p2, _ = fetch(src, t.transport, cursor=p1.next_cursor)
    assert len(p2.records) == 1 and p2.next_cursor is None
    sent = t.requests[0].url.params
    assert sent["cursor"] == "*" and sent["mailto"] == "me@example.org" and sent["api_key"] == "k-123"
    stored = src.build_query(EXPR, NF).params
    assert "mailto" not in stored and "api_key" not in stored
    assert "api_key" not in public_url("https://api.openalex.org/works", {**stored, "api_key": "k", "mailto": "m"})


def test_openalex_normalisation():
    rec = OpenAlexSource().normalize(fx.OPENALEX_PAGE_1["results"][0])
    assert (rec.provider_record_id, rec.doi, rec.pmid, rec.openalex_id) == ("W1", fx.DOI_W1, "31111111", "W1")
    assert rec.access_status == "open_pdf_link" and rec.pdf_url.endswith("W1.pdf")
    assert rec.abstract == "Older adults with depression exercised." and rec.venue == "Journal of Tests"
    closed = OpenAlexSource().normalize(fx.OPENALEX_PAGE_1["results"][1])
    assert closed.oa_status == "closed" and closed.access_status == "metadata_only" and closed.pdf_url is None
    pre = OpenAlexSource().normalize(fx.OPENALEX_PAGE_2["results"][0])
    assert pre.is_preprint and pre.arxiv_id == "2101.00001"
    assert rebuild_abstract({"b": [1], "a": [0]}) == "a b" and rebuild_abstract(None) is None


def test_europepmc_stops_when_the_cursor_does_not_move_and_reads_oa_links():
    page, _ = fetch(EuropePMCSource(), fx.ScholarlyTransport().transport)
    assert len(page.records) == 2 and page.next_cursor is None and page.total == 2
    w1, e2 = (EuropePMCSource().normalize(r) for r in page.records)
    assert w1.provider_record_id == "MED:31111111" and w1.doi == fx.DOI_W1 and w1.oa_status == "unknown"
    assert w1.abstract == "Background Older adults with depression exercised." and w1.language == "en"
    assert e2.access_status == "open_pdf_link" and e2.pmcid == "PMC9000001"


def test_arxiv_parses_atom_and_labels_preprints():
    page, _ = fetch(ArxivSource(), fx.ScholarlyTransport().transport)
    assert page.total == 1 and page.next_cursor is None
    rec = ArxivSource().normalize(page.records[0])
    assert rec.arxiv_id == "2101.00001" and rec.is_preprint and rec.pdf_url == "https://arxiv.org/pdf/2101.00001v2"
    assert page.records[0]["atom_xml"].startswith("<")


def test_arxiv_refuses_dtds_and_reports_query_errors():
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><feed xmlns="http://www.w3.org/2005/Atom"/>'
    with pytest.raises(ProviderError) as e:
        fetch(ArxivSource(), httpx.MockTransport(lambda r: httpx.Response(200, text=evil)))
    assert e.value.kind == "malformed"
    err = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#bad</id>'
        "<title>Error</title></entry></feed>"
    )
    with pytest.raises(ProviderError) as e:
        fetch(ArxivSource(), httpx.MockTransport(lambda r: httpx.Response(200, text=err)))
    assert e.value.kind == "rejected"


def test_crossref_treats_a_link_as_open_only_with_an_open_licence():
    page, _ = fetch(CrossrefSource(), fx.ScholarlyTransport().transport)
    w2, x2 = (CrossrefSource().normalize(r) for r in page.records)
    assert w2.oa_status == "unknown" and w2.access_status == "metadata_only" and w2.year == 2019
    assert x2.oa_status == "open" and x2.pdf_url == "https://publisher.example.org/x2.pdf"
    with pytest.raises(RecordError):
        CrossrefSource().record_id({"title": ["no doi"]})


@pytest.mark.parametrize(
    "body",
    [{"results": "nope"}, ["a", "list"], {"meta": {}}],
)
def test_malformed_openalex_bodies_raise_a_malformed_provider_error(body):
    with pytest.raises(ProviderError) as e:
        fetch(OpenAlexSource(), httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    assert e.value.kind == "malformed"


def test_non_json_body_is_malformed():
    with pytest.raises(ProviderError) as e:
        fetch(OpenAlexSource(), httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")))
    assert e.value.kind == "malformed"


def test_retries_429_and_5xx_then_succeeds():
    calls = {"n": 0}

    def handler(r):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        if calls["n"] == 2:
            return httpx.Response(502)
        return httpx.Response(200, json=fx.OPENALEX_PAGE_2)

    page, http = fetch(OpenAlexSource(), httpx.MockTransport(handler))
    assert len(page.records) == 1 and calls["n"] == 3 and http.requests_made == 3


@pytest.mark.parametrize(("status", "kind"), [(503, "server_error"), (429, "rate_limited")])
def test_retries_are_bounded(status, kind):
    calls = {"n": 0}

    def handler(r):
        calls["n"] += 1
        return httpx.Response(status)

    with pytest.raises(ProviderError) as e:
        fetch(OpenAlexSource(), httpx.MockTransport(handler))
    assert e.value.kind == kind and calls["n"] == 3 and "after 3 attempts" in e.value.summary()


def test_client_errors_are_not_retried():
    calls = {"n": 0}

    def handler(r):
        calls["n"] += 1
        return httpx.Response(400)

    with pytest.raises(ProviderError) as e:
        fetch(OpenAlexSource(), httpx.MockTransport(handler))
    assert e.value.kind == "rejected" and calls["n"] == 1


def test_timeouts_and_network_errors_become_provider_errors():
    def timeout(r):
        raise httpx.ReadTimeout("slow", request=r)

    def down(r):
        raise httpx.ConnectError("refused", request=r)

    for handler, kind in ((timeout, "timeout"), (down, "network")):
        with pytest.raises(ProviderError) as e:
            fetch(OpenAlexSource(), httpx.MockTransport(handler))
        assert e.value.kind == kind


def test_oversized_responses_are_refused():
    http = no_wait_fetcher(httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 2000)))
    http.max_bytes = 1000
    with pytest.raises(ProviderError) as e:
        http.get("OpenAlex", "https://api.openalex.org/works", {}, min_interval_s=0)
    assert e.value.kind == "too_large"


def test_registry_respects_the_enabled_list_and_maps_protocol_source_names():
    class S:
        scholarly_contact_email = None
        openalex_api_key = None
        search_providers_enabled = "openalex, europepmc"

    reg = registry(S())
    assert list(reg) == ["openalex", "europepmc"]
    matched, unsupported = match_protocol_sources(["PubMed", "Open-Alex", "Scopus", "MEDLINE"], reg)
    assert matched == ["europepmc", "openalex"] and unsupported == ["Scopus"]
