"""Gemini adapter (mock transport) and the gateway's data-class gate. No network."""

import json

import httpx
import pytest

from app.providers.llm import build_gateway, describe
from app.providers.llm.base import LLMError, LLMGateway
from app.providers.llm.gemini import GeminiProvider
from app.providers.scholarly.throttle import LocalThrottle
from tests.fakes import FakeLLM


def ok_body(data, finish="STOP"):
    return {
        "candidates": [{"content": {"parts": [{"text": json.dumps(data)}]}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 42, "candidatesTokenCount": 7},
        "modelVersion": "gemini-x-001",
    }


def provider(handler, **kw):
    return GeminiProvider(
        api_key="TEST-KEY",
        model="gemini-x",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        throttle=LocalThrottle(sleep=lambda s: None),
        sleep=lambda s: None,
        min_interval_s=0,
        **kw,
    )


def call(p):
    return p.generate_json(system="s", prompt="p", schema={"type": "OBJECT"}, temperature=0, max_output_tokens=100)


def test_success_parses_json_and_usage_and_sends_the_key_only_in_a_header():
    seen = []

    def handler(r):
        seen.append(r)
        return httpx.Response(200, json=ok_body({"assessments": []}))

    res = call(provider(handler))
    assert res.data == {"assessments": []} and res.input_tokens == 42 and res.model == "gemini-x-001"
    r = seen[0]
    assert r.headers["x-goog-api-key"] == "TEST-KEY" and "TEST-KEY" not in str(r.url)
    body = json.loads(r.content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert str(r.url).endswith("/models/gemini-x:generateContent")


def test_429_with_retry_info_is_retried():
    calls = {"n": 0}

    def handler(r):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"details": [{"retryDelay": "0s"}]}})
        return httpx.Response(200, json=ok_body({"assessments": []}))

    assert call(provider(handler)).attempts == 2


@pytest.mark.parametrize(
    ("status", "phrase"),
    [
        (400, "rejected the request"),
        (401, "rejected the API key"),
        (403, "refused access"),
        (404, "does not offer model"),
    ],
)
def test_client_errors_are_explained_and_not_retried(status, phrase):
    calls = {"n": 0}

    def handler(r):
        calls["n"] += 1
        return httpx.Response(status)

    with pytest.raises(LLMError) as e:
        call(provider(handler))
    assert e.value.kind == "rejected" and phrase in e.value.message and calls["n"] == 1
    assert "TEST-KEY" not in e.value.message


def test_quota_exhaustion_after_retries_is_rate_limited():
    with pytest.raises(LLMError) as e:
        call(provider(lambda r: httpx.Response(429), max_retries=2))
    assert e.value.kind == "rate_limited"


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ({"promptFeedback": {"blockReason": "SAFETY"}}, "blocked"),
        (ok_body({"a": 1}, finish="MAX_TOKENS"), "blocked"),
        ({"candidates": [{"content": {"parts": [{"text": "not json"}]}, "finishReason": "STOP"}]}, "malformed"),
        ({"candidates": [{"content": {"parts": [{"text": "[1, 2]"}]}, "finishReason": "STOP"}]}, "malformed"),
    ],
)
def test_unusable_answers_raise(body, kind):
    with pytest.raises(LLMError) as e:
        call(provider(lambda r: httpx.Response(200, json=body)))
    assert e.value.kind == kind


def test_gateway_refuses_private_documents_and_unconfigured_use():
    gw = LLMGateway(FakeLLM())
    with pytest.raises(LLMError) as e:
        gw.generate_json(data_class="private_document", system="s", prompt="p", schema={})
    assert e.value.kind == "refused"
    with pytest.raises(LLMError) as e:
        LLMGateway(None).generate_json(data_class="public_metadata", system="s", prompt="p", schema={})
    assert e.value.kind == "not_configured"
    assert gw.generate_json(data_class="public_metadata", system="s", prompt="--- P1\nx", schema={}).data


class S:
    llm_provider = "gemini"
    llm_api_key = "SECRET-KEY-VALUE-123"
    llm_model = "m"
    llm_base_url = "https://example.invalid/v1beta"
    llm_timeout_s = 5
    llm_min_interval_s = 0
    llm_max_retries = 0
    llm_send_private_documents = False
    redis_url = "redis://127.0.0.1:1/0"


def test_describe_and_build_gateway_never_expose_the_key():
    d = describe(S())
    assert d["configured"] and d["model"] == "m" and "SECRET-KEY-VALUE-123" not in json.dumps(d)
    assert "free tier" in d["provider_notice"]
    assert build_gateway(S()).available
    off = S()
    off.llm_provider = "none"
    assert not describe(off)["configured"] and not build_gateway(off).available
