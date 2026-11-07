import pytest

from app.assist.commands import interpret_rules, step_from


@pytest.mark.parametrize(
    ("text", "action", "view"),
    [
        ("run the search", "start_search", None),
        ("Please go to screening", "open_view", "screening"),
        ("open the evidence table", "open_view", "tables"),
        ("take me to the PRISMA flow", "open_view", "flow"),
        ("open extraction form", "open_view", "extraction"),
        ("next step", "next_step", None),
        ("go back", "previous_step", None),
        ("suggest better keywords for the search", "suggest_search_query", None),
        ("assess relevance of the papers", "assess_relevance", None),
        ("extract the data", "run_extraction", None),
        ("appraise the studies", "suggest_appraisal", None),
        ("draft the synthesis", "draft_synthesis", None),
        ("generate the report", "generate_report", None),
        ("screening", "open_view", "screening"),
    ],
)
def test_rules_recognise_commands(text, action, view):
    cmd = interpret_rules(text, "search")
    assert cmd is not None and cmd.action == action and cmd.view == view


def test_search_tier_and_non_commands():
    assert interpret_rules("start a search of additional sources", None).params == {"tier": "additional"}
    for text in (
        "what is a systematic review?",
        "to find out if deep learning works in practice",
        "Use Other / custom.",
    ):
        assert interpret_rules(text, None) is None


def test_strict_mode_never_swallows_protocol_answers():
    for answer in ("Open-access sources", "Screening only", "deep learning in radiology", "Open access search only"):
        assert interpret_rules(answer, None, strict=True) is None
    assert interpret_rules("open screening", None, strict=True).action == "open_view"
    assert interpret_rules("next step", None, strict=True).action == "next_step"


def test_step_order():
    assert step_from("protocol", 1) == "search"
    assert step_from("papers", 1) == "screening"  # a view belongs to its step
    assert step_from("report", 1) is None and step_from("protocol", -1) is None
