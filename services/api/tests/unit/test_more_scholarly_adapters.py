"""Adapters added for broader coverage. Responses are hand-written in each provider's documented format."""

import json

import httpx
import pytest

from app.providers.scholarly import ALL_KEYS, is_default_on, registry
from app.providers.scholarly.core import CORESource
from app.providers.scholarly.dblp import DBLPSource
from app.providers.scholarly.doaj import DOAJSource
from app.providers.scholarly.eric import ERICSource
from app.providers.scholarly.hal import HALSource
from app.providers.scholarly.http import ProviderError
from app.providers.scholarly.openaire import OpenAIRESource
from app.providers.scholarly.plos import PLOSSource
from app.providers.scholarly.pubmed import PubMedSource
from app.providers.scholarly.semanticscholar import SemanticScholarSource
from app.search import query as q
from app.search.derive import FilterPlan
from tests.fakes import no_wait_fetcher

EXPR = q.parse('("tai chi" OR taiji) AND (balance OR falls) NOT dance')
NF = FilterPlan(None, None, [], [], [])
DATES = FilterPlan(2015, 2020, [("en", "eng")], [], [])


class Recorder:
    def __init__(self, handler):
        self.requests: list[httpx.Request] = []
        self.handler = handler
        self.transport = httpx.MockTransport(self._h)

    def _h(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def run(source, handler, cursor=None, size=50, filters=NF):
    rec = Recorder(handler)
    page = source.fetch_page(no_wait_fetcher(rec.transport), source.build_query(EXPR, filters), cursor, size)
    return page, rec


def test_semantic_scholar_uses_its_boolean_syntax_key_header_and_token_paging():
    pq = SemanticScholarSource().build_query(EXPR, DATES)
    assert pq.query_text == '("tai chi" | taiji) + (balance | falls) -dance'
    assert pq.params["year"] == "2015-2020" and pq.params["sort"] == "citationCount:desc"
    body = {
        "total": 2,
        "token": "NEXT",
        "data": [
            {
                "paperId": "abc",
                "title": "Tai chi and falls",
                "abstract": "An <b>abstract</b>",
                "year": 2018,
                "authors": [{"name": "Ann Lee"}],
                "externalIds": {"DOI": "10.1234/ABC", "PubMed": "123"},
                "openAccessPdf": {"url": "https://x.org/a.pdf"},
                "isOpenAccess": True,
                "venue": "J Test",
            }
        ],
    }
    page, rec = run(SemanticScholarSource(api_key="s2key"), lambda r: httpx.Response(200, json=body))
    assert rec.requests[0].headers["x-api-key"] == "s2key" and "s2key" not in str(rec.requests[0].url)
    assert page.next_cursor == "NEXT" and page.total == 2
    r = SemanticScholarSource().normalize(page.records[0])
    assert (r.doi, r.pmid, r.oa_status, r.pdf_url, r.abstract) == (
        "10.1234/abc",
        "123",
        "open",
        "https://x.org/a.pdf",
        "An abstract",
    )
    page2, rec2 = run(SemanticScholarSource(), lambda r: httpx.Response(200, json=body), cursor="NEXT")
    assert rec2.requests[0].url.params["token"] == "NEXT" and "x-api-key" not in rec2.requests[0].headers


PUBMED_XML = """<?xml version="1.0" ?>
<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, 1st January 2024//EN"
 "https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_240101.dtd">
<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>3001</PMID><Article>
<Journal><JournalIssue><PubDate><Year>2019</Year></PubDate></JournalIssue><Title>Age Ageing</Title></Journal>
<ArticleTitle>Tai chi for <i>fall</i> prevention</ArticleTitle>
<Abstract><AbstractText Label="BACKGROUND">Falls are common.</AbstractText>
<AbstractText Label="RESULTS">Fewer falls.</AbstractText></Abstract>
<AuthorList><Author><LastName>Smith</LastName><ForeName>Ann</ForeName></Author></AuthorList>
<Language>eng</Language>
<PublicationTypeList><PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList>
</Article></MedlineCitation><PubmedData><ArticleIdList>
<ArticleId IdType="doi">10.9999/tc</ArticleId><ArticleId IdType="pmc">PMC77</ArticleId>
</ArticleIdList></PubmedData>
</PubmedArticle></PubmedArticleSet>"""


def test_pubmed_searches_then_fetches_and_parses_xml_with_an_external_doctype():
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"count": "1", "idlist": ["3001"]}})
        assert r.url.params["id"] == "3001"
        return httpx.Response(200, text=PUBMED_XML)

    pq = PubMedSource().build_query(EXPR, DATES)
    assert pq.params["mindate"] == "2015" and "english[lang]" in pq.query_text and "dance" in pq.query_text
    page, rec = run(PubMedSource(api_key="nk", email="me@x.org"), handler)
    assert all(r.url.params["api_key"] == "nk" for r in rec.requests)
    assert page.next_cursor is None and page.total == 1
    r = PubMedSource().normalize(page.records[0])
    assert r.title == "Tai chi for fall prevention" and r.pmcid == "PMC77" and r.oa_status == "open"
    assert r.abstract == "BACKGROUND: Falls are common. RESULTS: Fewer falls." and r.language == "en"


def test_pubmed_refuses_xml_with_an_internal_entity_declaration():
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><PubmedArticleSet/>'

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"count": "1", "idlist": ["1"]}})
        return httpx.Response(200, text=evil)

    with pytest.raises(ProviderError):
        run(PubMedSource(), handler)


def test_doaj_puts_the_query_in_the_path_and_pages_by_number():
    body = {
        "total": 150,
        "results": [
            {
                "id": "d1",
                "bibjson": {
                    "title": "Taiji and balance",
                    "abstract": "Text",
                    "year": "2017",
                    "author": [{"name": "B. Chen"}],
                    "identifier": [{"type": "doi", "id": "10.5555/d1"}],
                    "link": [{"type": "fulltext", "url": "https://j.org/d1"}],
                    "journal": {"title": "Open J", "language": ["EN"]},
                },
            }
        ],
    }
    page, rec = run(DOAJSource(), lambda r: httpx.Response(200, json=body), size=100)
    assert "%22tai%20chi%22" in str(rec.requests[0].url) and rec.requests[0].url.params["page"] == "1"
    assert page.next_cursor == "2"
    r = DOAJSource().normalize(page.records[0])
    assert r.oa_status == "open" and r.doi == "10.5555/d1" and r.oa_url == "https://j.org/d1"
    assert "bibjson.year:[2015 TO 2020]" in DOAJSource().build_query(EXPR, DATES).query_text


def test_openaire_cursor_paging_and_access_rights():
    body = {
        "header": {"numFound": 3, "nextCursor": "c2"},
        "results": [
            {
                "id": "oa::1",
                "mainTitle": "Tai chi trial",
                "descriptions": ["Abstract here"],
                "authors": [{"fullName": "C. D."}],
                "publicationDate": "2016-05-01",
                "pids": [{"scheme": "doi", "value": "10.7777/oa1"}],
                "bestAccessRight": {"label": "OPEN"},
                "instances": [{"urls": ["https://repo.eu/1"]}],
            }
        ],
    }
    page, rec = run(OpenAIRESource(), lambda r: httpx.Response(200, json=body))
    assert rec.requests[0].url.params["cursor"] == "*" and page.next_cursor == "c2"
    r = OpenAIRESource().normalize(page.records[0])
    assert (r.doi, r.year, r.oa_status, r.oa_url) == ("10.7777/oa1", 2016, "open", "https://repo.eu/1")


def solr(docs, total):
    return {"response": {"numFound": total, "docs": docs}}


def test_plos_eric_and_hal_page_with_start_and_rows():
    plos = solr(
        [{"id": "10.1371/journal.pone.1", "title_display": "Tai chi", "abstract": ["Abs"], "journal": "PLOS ONE"}], 2
    )
    page, rec = run(PLOSSource(), lambda r: httpx.Response(200, json=plos))
    assert page.next_cursor == "1" and PLOSSource().normalize(page.records[0]).oa_status == "open"

    eric = solr([{"id": "ED500", "title": "Tai chi in schools", "e_fulltextauth": 1, "author": ["A"]}], 1)
    page, _ = run(ERICSource(), lambda r: httpx.Response(200, json=eric))
    r = ERICSource().normalize(page.records[0])
    assert page.next_cursor is None and r.pdf_url == "https://files.eric.ed.gov/fulltext/ED500.pdf"

    hal = solr([{"halId_s": "hal-1", "title_s": ["Taiji"], "fileMain_s": "https://hal.science/hal-1/document"}], 1)
    page, rec = run(HALSource(), lambda r: httpx.Response(200, json=hal), filters=DATES)
    assert "producedDateY_i:[2015 TO 2020]" in rec.requests[0].url.params["fq"]
    assert HALSource().normalize(page.records[0]).oa_status == "open"


def test_dblp_sends_the_main_term_of_each_concept_and_parses_hits():
    pq = DBLPSource().build_query(EXPR, NF)
    assert pq.query_text == "tai chi balance" and any("NOT" in x for x in pq.limitations)
    body = {
        "result": {
            "hits": {
                "@total": "1",
                "hit": [
                    {
                        "info": {
                            "key": "conf/x/1",
                            "title": "Taiji robots.",
                            "year": "2020",
                            "authors": {"author": {"text": "D. E."}},
                        }
                    }
                ],
            }
        }
    }
    page, _ = run(DBLPSource(), lambda r: httpx.Response(200, json=body))
    r = DBLPSource().normalize(page.records[0])
    assert r.title == "Taiji robots" and r.authors[0].name == "D. E." and page.next_cursor is None


def test_core_sends_the_key_as_a_bearer_header_only():
    body = {"totalHits": 1, "results": [{"id": 9, "title": "Tai chi", "downloadUrl": "https://core.ac.uk/d/9.pdf"}]}
    page, rec = run(CORESource(api_key="ck"), lambda r: httpx.Response(200, json=body))
    assert rec.requests[0].headers["authorization"] == "Bearer ck" and "ck" not in str(rec.requests[0].url)
    r = CORESource(api_key="ck").normalize(page.records[0])
    assert r.pdf_url and r.oa_status == "open" and page.next_cursor is None


class S:
    scholarly_contact_email = None
    openalex_api_key = None
    semantic_scholar_api_key = None
    ncbi_api_key = None
    core_api_key = None
    scholarly_endpoints = ""
    search_providers_enabled = ",".join(ALL_KEYS)


def test_registry_offers_core_only_with_a_key_and_has_sensible_defaults():
    reg = registry(S())
    assert "core" not in reg and "semanticscholar" in reg and "dblp" in reg
    S2 = type("S2", (S,), {"core_api_key": "k"})
    assert "core" in registry(S2())
    defaults = {k for k, p in reg.items() if p.tier == "open_access" and is_default_on(k, p)}
    assert defaults == {"openalex", "europepmc", "semanticscholar", "doaj"}
    json.dumps([p.description for p in reg.values()])  # every adapter is presentable
