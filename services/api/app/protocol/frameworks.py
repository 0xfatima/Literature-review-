"""Question frameworks and the rule-based framework advisor. Stdlib only.

The advisor is deliberately simple and transparent: it looks for keywords in the user's own topic/objective
and explains which words it matched. It is NOT an AI judgement and the UI says so. A model-backed advisor can
replace it later behind the same `FrameworkAdvisor` protocol (rule 6) without changing services or the graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Element:
    key: str
    label: str
    help: str


@dataclass(frozen=True)
class Framework:
    key: str
    label: str
    description: str
    elements: tuple[Element, ...]


FRAMEWORKS: dict[str, Framework] = {
    f.key: f
    for f in (
        Framework(
            "pico",
            "PICO",
            "Population, Intervention, Comparison, Outcome. Common for questions about the effect of an intervention.",
            (
                Element("population", "Population", "Who is studied (e.g. adults over 60 with depression)."),
                Element("intervention", "Intervention", "What is done or given."),
                Element("comparison", "Comparison", "What it is compared with (may be 'none' or 'usual care')."),
                Element("outcome", "Outcome", "What is measured."),
            ),
        ),
        Framework(
            "peco",
            "PECO",
            "Population, Exposure, Comparator, Outcome. Common for questions about exposures or risk factors.",
            (
                Element("population", "Population", "Who is studied."),
                Element("exposure", "Exposure", "The exposure or risk factor of interest."),
                Element("comparator", "Comparator", "The unexposed or differently exposed group."),
                Element("outcome", "Outcome", "What is measured."),
            ),
        ),
        Framework(
            "spider",
            "SPIDER",
            "Sample, Phenomenon of Interest, Design, Evaluation, Research type. Suited to qualitative and "
            "mixed-methods questions.",
            (
                Element("sample", "Sample", "Who took part."),
                Element("phenomenon", "Phenomenon of interest", "The experience, behaviour or process studied."),
                Element("design", "Design", "How the data were collected (e.g. interviews, focus groups)."),
                Element("evaluation", "Evaluation", "What was explored (e.g. views, experiences, attitudes)."),
                Element("research_type", "Research type", "Qualitative, quantitative or mixed methods."),
            ),
        ),
        Framework(
            "custom",
            "Other / custom",
            "Define your own elements when none of the standard frameworks fits.",
            (),
        ),
        Framework("none", "Not applicable", "Do not use a question framework for this review.", ()),
    )
}
FRAMEWORK_KEYS = tuple(FRAMEWORKS)
MAX_CUSTOM_ELEMENTS = 8

# Keyword cues. Matched as whole words / stems in lower-cased text. Kept short and inspectable on purpose.
_CUES: dict[str, tuple[str, ...]] = {
    "spider": (
        "experience",
        "experiences",
        "lived",
        "perception",
        "perceptions",
        "perceive",
        "attitude",
        "attitudes",
        "views",
        "qualitative",
        "interview",
        "interviews",
        "focus group",
        "barriers",
        "facilitators",
        "meaning",
        "beliefs",
        "mixed methods",
        "mixed-methods",
        "understanding of",
    ),
    "peco": (
        "exposure",
        "exposed",
        "exposures",
        "risk factor",
        "risk factors",
        "associated with",
        "association",
        "pollution",
        "smoking",
        "occupational",
        "environmental",
        "air quality",
        "noise",
        "radiation",
        "diet",
    ),
    "pico": (
        "effect",
        "effects",
        "effectiveness",
        "efficacy",
        "intervention",
        "interventions",
        "treatment",
        "therapy",
        "programme",
        "program",
        "trial",
        "versus",
        "compared",
        "improve",
        "improves",
        "reduce",
        "reduces",
        "prevent",
        "training",
    ),
}
# Order used to break ties: the more specific frameworks first.
_PRIORITY = ("spider", "peco", "pico")


@dataclass(frozen=True)
class Suggestion:
    framework: str  # a key of FRAMEWORKS
    rationale: str
    matched_terms: tuple[str, ...] = field(default_factory=tuple)
    method: str = "keyword_rules_v1"


class FrameworkAdvisor(Protocol):
    def suggest(self, text: str) -> Suggestion: ...


def _matches(text: str, cue: str) -> bool:
    return re.search(r"(?<![a-z])" + re.escape(cue) + r"(?![a-z])", text) is not None


class KeywordFrameworkAdvisor:
    """Counts cue words per framework; the highest count wins (ties broken by _PRIORITY). No match -> 'custom'
    with an explanation, never a confident-sounding guess."""

    def suggest(self, text: str) -> Suggestion:
        lowered = re.sub(r"\s+", " ", text.lower())
        hits = {fw: tuple(c for c in cues if _matches(lowered, c)) for fw, cues in _CUES.items()}
        best = max(_PRIORITY, key=lambda fw: (len(hits[fw]), -_PRIORITY.index(fw)))
        if not hits[best]:
            return Suggestion(
                "custom",
                "No wording in the topic or objective pointed to a standard framework. Choose one, define your "
                "own elements, or skip the framework.",
            )
        terms = hits[best]
        label = FRAMEWORKS[best].label
        reason = {
            "spider": "the topic is about experiences, views or qualitative evidence",
            "peco": "the topic is about an exposure or risk factor rather than a deliberate intervention",
            "pico": "the topic is about the effect of an intervention",
        }[best]
        quoted = ", ".join(f"'{t}'" for t in terms[:5])
        return Suggestion(best, f"{label} is suggested because {reason} (matched words: {quoted}).", terms)
