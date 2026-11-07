import pytest

from app.core import secretbox
from app.core.email_address import check_syntax, validate_deliverable
from app.core.errors import ValidationFailed
from app.providers.llm import LLMResult
from app.search import llm_query
from app.search import query as q


@pytest.mark.parametrize(
    "email",
    ["ann.lee@gmail.com", "ann.lee+review@gmail.com", "x.y@uni.edu.pk", "first_last@company.co.uk"],
)
def test_valid_addresses_pass(email):
    check_syntax(email)


@pytest.mark.parametrize(
    ("email", "issue"),
    [
        ("abc@gmail.com", "invalid_gmail"),  # Gmail names have at least 6 characters
        ("ann_lee@gmail.com", "invalid_gmail"),  # no underscores in Gmail
        ("someone@gmial.com", "domain_typo"),
        ("x@mailinator.com", "disposable"),
        ("a..b@uni.edu", "invalid_local_part"),
        ("a@-bad.com", "invalid_domain"),
        ("a@nodot", "invalid"),
    ],
)
def test_impossible_addresses_are_refused_with_a_reason(email, issue):
    with pytest.raises(ValidationFailed) as e:
        check_syntax(email) if "@" in email and "." in email.split("@")[1] else validate_deliverable(
            email, check_dns=False, timeout_s=1
        )
    assert e.value.details[0]["issue"] in (issue, "invalid", "invalid_domain")


def test_dns_lookup_result_is_respected_but_unknown_never_blocks():
    validate_deliverable("a@uni.edu", check_dns=True, timeout_s=1, lookup=lambda d, t: None)
    validate_deliverable("a@uni.edu", check_dns=True, timeout_s=1, lookup=lambda d, t: True)
    with pytest.raises(ValidationFailed):
        validate_deliverable("a@uni.edu", check_dns=True, timeout_s=1, lookup=lambda d, t: False)
    with pytest.raises(ValidationFailed):
        validate_deliverable("a@example.com", check_dns=False, timeout_s=1)
    validate_deliverable("a@example.com", check_dns=False, timeout_s=1, allow_reserved=True)


class S:
    user_secrets_key = None
    session_secret = "s" * 40
    app_env = "production"


def test_secretbox_round_trip_and_key_change():
    token = secretbox.encrypt(S(), "AIza-secret-123456")
    assert "secret" not in token and secretbox.decrypt(S(), token) == "AIza-secret-123456"
    other = type("O", (S,), {"session_secret": "t" * 40})()
    with pytest.raises(secretbox.SecretUnavailable):
        secretbox.decrypt(other, token)
    assert secretbox.hint("AIza-secret-123456") == "…3456" and secretbox.hint("short") == "…"


class FakeGW:
    def __init__(self, data):
        self.data, self.prompts = data, []

    def generate_json(self, **kw):
        self.prompts.append(kw)
        return LLMResult(provider="fake", model="m", data=self.data)


CONTENT = {"objective": "Effect of tai chi on balance in older adults", "questions": [{"text": "Does tai chi help?"}]}


def test_ai_expression_is_validated_and_used():
    gw = FakeGW(
        {"concepts": [], "expression": '("tai chi" OR taiji) AND (balance OR falls) AND (elderly OR "older adults")'}
    )
    s = llm_query.suggest(gw, CONTENT, "Tai chi")
    assert s is not None and q.parse(s.expression)
    assert gw.prompts[0]["data_class"] == "public_metadata" and "tai chi" in gw.prompts[0]["prompt"]


def test_invalid_ai_expression_is_rebuilt_from_sanitised_concepts():
    gw = FakeGW(
        {
            "expression": "TITLE:(tai chi",
            "concepts": [
                {"name": "Intervention", "terms": ["tai chi", "taiji", 'qigong" OR x:y']},
                {"name": "Outcome", "terms": ["balance", "postural control"]},
            ],
        }
    )
    s = llm_query.suggest(gw, CONTENT, "t")
    assert s.expression == '("tai chi" OR taiji OR "qigong or x y") AND (balance OR "postural control")'
    assert any("rebuilt" in n for n in s.notes)


def test_unusable_ai_answer_returns_none():
    assert llm_query.suggest(FakeGW({"expression": "((", "concepts": []}), CONTENT, "t") is None


def test_core_terms_take_one_synonym_per_concept():
    node = q.parse('("tai chi" OR taiji) AND (balance OR falls) NOT dance')
    assert [t.text for t in q.core_terms(node)] == ["tai chi", "balance"]
    assert [t.text for t in q.core_terms(node, 2)] == ["tai chi", "taiji", "balance", "falls"]
