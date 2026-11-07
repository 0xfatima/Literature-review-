"""OpenAI-compatible and Anthropic adapters, the provider catalog and per-provider gateway construction."""

import json

import httpx
import pytest

from app.providers.llm import LLMError, build_gateway, catalog, describe
from app.providers.llm.anthropic import AnthropicProvider
from app.providers.llm.jsonutil import parse_json_object, to_json_schema
from app.providers.llm.openai_compat import OpenAICompatProvider
from app.providers.scholarly.throttle import LocalThrottle

SCHEMA = {"type": "OBJECT", "properties": {"ok": {"type": "BOOLEAN"}}, "required": ["ok"]}


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def openai(handler, **kw):
    return OpenAICompatProvider(
        name="groq",
        api_key="sk-test",
        model="m",
        base_url="https://api.example/v1",
        client=client(handler),
        throttle=LocalThrottle(sleep=lambda _s: None),
        sleep=lambda _s: None,
        max_retries=1,
        **kw,
    )


def chat(text, finish="stop"):
    return {
        "model": "m",
        "choices": [{"message": {"content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }


def call(p):
    return p.generate_json(system="s", prompt="p", schema=SCHEMA, temperature=0, max_output_tokens=100)


def test_openai_compatible_sends_bearer_key_schema_and_json_mode():
    seen = []

    def h(r):
        seen.append(json.loads(r.content))
        assert r.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(200, json=chat('```json\n{"ok": true}\n```'))

    res = call(openai(h))
    assert res.data == {"ok": True} and res.input_tokens == 5
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert '"type":"boolean"' in seen[0]["messages"][0]["content"]


def test_openai_compatible_retries_without_json_mode_when_the_model_rejects_it():
    calls = []

    def h(r):
        body = json.loads(r.content)
        calls.append("response_format" in body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "unsupported"}})
        return httpx.Response(200, json=chat('Sure: {"ok": false}'))

    assert call(openai(h)).data == {"ok": False} and calls == [True, False]


def test_openai_compatible_maps_errors():
    with pytest.raises(LLMError) as e:
        call(openai(lambda r: httpx.Response(401, json={})))
    assert e.value.kind == "rejected" and "API key" in e.value.message and "sk-test" not in e.value.message
    with pytest.raises(LLMError) as e:
        call(openai(lambda r: httpx.Response(429, json={})))
    assert e.value.kind == "rate_limited"
    with pytest.raises(LLMError) as e:
        call(openai(lambda r: httpx.Response(200, json=chat('{"ok": tr', finish="length"))))
    assert e.value.kind == "blocked"


def test_anthropic_adapter():
    def h(r):
        assert r.headers["x-api-key"] == "ak" and r.headers["anthropic-version"]
        return httpx.Response(
            200,
            json={
                "model": "c",
                "content": [{"type": "text", "text": '{"ok": true}'}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )

    p = AnthropicProvider(
        api_key="ak",
        model="c",
        base_url="https://a.example/v1",
        client=client(h),
        throttle=LocalThrottle(sleep=lambda _s: None),
        sleep=lambda _s: None,
    )
    assert call(p).data == {"ok": True}


def test_json_helpers():
    assert to_json_schema(SCHEMA)["properties"]["ok"]["type"] == "boolean"
    assert parse_json_object('noise {"a": 1} trailing') == {"a": 1}
    with pytest.raises(LLMError):
        parse_json_object("[1, 2]")


class Settings:
    llm_provider = "groq"
    llm_api_key = "gk"
    llm_model = "llama-3.3-70b-versatile"
    llm_base_url = "https://generativelanguage.googleapis.com/v1beta"
    llm_timeout_s = 5
    llm_min_interval_s = 0.0
    llm_max_retries = 0
    llm_send_private_documents = False
    llm_key_source = "user"
    redis_url = "redis://127.0.0.1:1/0"
    llm_max_concurrent_calls = 2
    llm_global_max_concurrent_calls = 0
    app_base_url = "http://localhost:3000"


def test_gateway_builds_every_supported_provider_with_its_fixed_base_url():
    for name in ("gemini", "groq", "openrouter", "openai", "anthropic"):
        s = type("S", (Settings,), {"llm_provider": name})()
        gw = build_gateway(s)
        assert gw.available and gw.provider.name == name
        if name != "gemini":
            assert gw.provider._base == catalog.PROVIDERS[name].base_url  # noqa: SLF001
    d = describe(Settings())
    assert d["key_source"] == "user" and d["pricing"] == "free" and "gk" not in json.dumps(d)


def test_catalog_lists_free_models_first_and_reads_openrouter_prices_live():
    def h(r):
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "x/paid", "name": "Paid", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
                    {"id": "y/free:free", "name": "Free", "pricing": {"prompt": "0", "completion": "0"}},
                ]
            },
        )

    models, source = catalog.list_models("openrouter", None, client(h))
    assert source == "live" and [m["pricing"] for m in models] == ["free", "paid"]
    models, source = catalog.list_models("gemini", None, client(lambda r: httpx.Response(500)))
    assert source == "static" and models[0]["id"] == catalog.PROVIDERS["gemini"].default_model
    assert models[0]["pricing"] == "free"
    assert (
        catalog.static_pricing("openrouter", "a/b:free") == "free" and catalog.static_pricing("openai", "z") == "paid"
    )
    assert catalog.PROVIDERS[catalog.DEFAULT_PROVIDER].models[0].pricing == "free"
