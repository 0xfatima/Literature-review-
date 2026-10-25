"""Design-specific critical-appraisal frameworks. Stdlib only.

There is deliberately NO single quality score: each framework has its own domains and judgement scale, and the
overall judgement is categorical. For domain-based risk-of-bias tools the overall judgement is SUGGESTED by the
worst-domain rule (the reviewer may depart from it with a justification); checklists have no computed overall at all.

The domain structures follow published tools (named in `based_on`), but the prompts are short paraphrases written for
this application, not the tools' official wording. Reviewers should apply the official guidance at `reference_url`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Domain:
    key: str
    label: str
    prompt: str


@dataclass(frozen=True)
class Framework:
    key: str
    version: str
    label: str
    designs: tuple[str, ...]
    based_on: str
    reference_url: str
    judgements: tuple[str, ...]  # domain scale, best -> worst, then "no information"-type values last
    domains: tuple[Domain, ...]
    overall_options: tuple[str, ...]
    # domain judgement -> overall option, ordered by severity (best first); None = checklist, no computed overall
    severity: tuple[str, ...] | None = None
    overall_map: dict[str, str] = field(default_factory=dict)
    kind: str = "risk_of_bias"  # risk_of_bias | checklist

    def view(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "version": self.version,
            "label": self.label,
            "kind": self.kind,
            "designs": list(self.designs),
            "based_on": self.based_on,
            "reference_url": self.reference_url,
            "judgements": list(self.judgements),
            "overall_options": list(self.overall_options),
            "overall_rule": (
                "Suggested overall = the most serious domain judgement; you may depart from it with a justification."
                if self.severity
                else "Checklist: no overall score is computed. Choose an overall level of concern and justify it."
            ),
            "domains": [{"key": d.key, "label": d.label, "prompt": d.prompt} for d in self.domains],
        }


CHECKLIST_OVERALL = ("no_major_concerns", "some_concerns", "major_concerns")

FRAMEWORKS: dict[str, Framework] = {
    f.key: f
    for f in (
        Framework(
            key="rct_rob",
            version="1",
            label="Randomised trials: risk of bias",
            designs=("randomised controlled trial", "cluster randomised trial", "crossover trial"),
            based_on="Domain structure of the Cochrane RoB 2 tool",
            reference_url="https://www.riskofbias.info/welcome/rob-2-0-tool",
            judgements=("low", "some_concerns", "high", "no_information"),
            domains=(
                Domain(
                    "randomisation",
                    "Randomisation process",
                    "Was allocation random and concealed, with baseline balance consistent with chance?",
                ),
                Domain(
                    "deviations",
                    "Deviations from intended interventions",
                    "Could knowledge of the assigned intervention or departures from it have affected the results?",
                ),
                Domain(
                    "missing_outcome_data",
                    "Missing outcome data",
                    "Were outcome data available for (nearly) all "
                    "participants, or is the result robust to what is missing?",
                ),
                Domain(
                    "outcome_measurement",
                    "Measurement of the outcome",
                    "Was the outcome measured appropriately "
                    "and in the same way across groups, without influence of knowing the intervention?",
                ),
                Domain(
                    "selection_of_result",
                    "Selection of the reported result",
                    "Was the reported result chosen from several measurements or analyses on the basis of the results?",
                ),
            ),
            overall_options=("low", "some_concerns", "high"),
            severity=("low", "no_information", "some_concerns", "high"),
            overall_map={
                "low": "low",
                "no_information": "some_concerns",
                "some_concerns": "some_concerns",
                "high": "high",
            },
        ),
        Framework(
            key="nrsi_rob",
            version="1",
            label="Non-randomised studies of interventions: risk of bias",
            designs=("non-randomised controlled study", "controlled before-after", "interrupted time series"),
            based_on="Domain structure of the ROBINS-I tool",
            reference_url="https://www.riskofbias.info/welcome/home",
            judgements=("low", "moderate", "serious", "critical", "no_information"),
            domains=(
                Domain("confounding", "Confounding", "Were important confounders measured and controlled for?"),
                Domain(
                    "selection",
                    "Selection of participants",
                    "Was selection into the study related to both intervention and outcome?",
                ),
                Domain(
                    "classification",
                    "Classification of interventions",
                    "Were intervention groups clearly and "
                    "correctly defined, without classification influenced by the outcome?",
                ),
                Domain(
                    "deviations",
                    "Deviations from intended interventions",
                    "Were there departures from the intended intervention beyond what would occur in usual practice?",
                ),
                Domain(
                    "missing_data",
                    "Missing data",
                    "Were outcome, intervention or confounder data missing in a way that could bias the result?",
                ),
                Domain(
                    "outcome_measurement",
                    "Measurement of outcomes",
                    "Could outcome measurement have been influenced by knowledge of the intervention received?",
                ),
                Domain(
                    "selection_of_result",
                    "Selection of the reported result",
                    "Is the reported result likely to have been selected from several analyses?",
                ),
            ),
            overall_options=("low", "moderate", "serious", "critical"),
            severity=("low", "moderate", "no_information", "serious", "critical"),
            overall_map={
                "low": "low",
                "moderate": "moderate",
                "no_information": "serious",
                "serious": "serious",
                "critical": "critical",
            },
        ),
        Framework(
            key="observational_nos",
            version="1",
            label="Cohort and case-control studies: selection, comparability, outcome",
            designs=("cohort study", "case-control study"),
            based_on="Domain structure of the Newcastle-Ottawa Scale (judged per domain; no stars are summed)",
            reference_url="https://www.ohri.ca/programs/clinical_epidemiology/oxford.asp",
            judgements=("adequate", "partly_adequate", "inadequate", "unclear"),
            domains=(
                Domain(
                    "selection",
                    "Selection",
                    "Were the groups representative and selected from the same "
                    "population, with exposure (or case status) ascertained reliably?",
                ),
                Domain(
                    "comparability",
                    "Comparability",
                    "Were groups comparable on the most important factors, by design or analysis?",
                ),
                Domain(
                    "outcome_exposure",
                    "Outcome / exposure",
                    "Were outcomes (or exposures) assessed reliably, "
                    "with adequate follow-up and similar ascertainment across groups?",
                ),
            ),
            overall_options=("low_concern", "some_concerns", "high_concern"),
            severity=("adequate", "partly_adequate", "unclear", "inadequate"),
            overall_map={
                "adequate": "low_concern",
                "partly_adequate": "some_concerns",
                "unclear": "some_concerns",
                "inadequate": "high_concern",
            },
        ),
        Framework(
            key="cross_sectional_checklist",
            version="1",
            label="Analytical cross-sectional studies: checklist",
            designs=("cross-sectional study", "survey"),
            based_on="Item structure of the JBI checklist for analytical cross-sectional studies",
            reference_url="https://jbi.global/critical-appraisal-tools",
            judgements=("yes", "no", "unclear", "not_applicable"),
            domains=(
                Domain("inclusion_criteria", "Inclusion criteria", "Were the criteria for inclusion clearly defined?"),
                Domain("setting", "Subjects and setting", "Were the subjects and setting described in detail?"),
                Domain(
                    "exposure_measurement", "Exposure measurement", "Was the exposure measured validly and reliably?"
                ),
                Domain(
                    "condition_criteria",
                    "Condition criteria",
                    "Were objective, standard criteria used to measure the condition?",
                ),
                Domain("confounders", "Confounders identified", "Were confounding factors identified?"),
                Domain(
                    "confounder_strategy",
                    "Dealing with confounders",
                    "Were strategies to deal with confounding stated?",
                ),
                Domain("outcome_measurement", "Outcome measurement", "Were outcomes measured validly and reliably?"),
                Domain("statistics", "Statistical analysis", "Was appropriate statistical analysis used?"),
            ),
            overall_options=CHECKLIST_OVERALL,
            kind="checklist",
        ),
        Framework(
            key="qualitative_checklist",
            version="1",
            label="Qualitative studies: checklist",
            designs=("qualitative study", "interview study", "focus group study"),
            based_on="Question structure of the CASP qualitative studies checklist",
            reference_url="https://casp-uk.net/casp-tools-checklists/",
            judgements=("yes", "no", "cant_tell"),
            domains=(
                Domain("aims", "Aims", "Was there a clear statement of the aims?"),
                Domain("methodology", "Methodology", "Is a qualitative methodology appropriate?"),
                Domain("design", "Design", "Was the design appropriate to address the aims?"),
                Domain("recruitment", "Recruitment", "Was the recruitment strategy appropriate?"),
                Domain("data_collection", "Data collection", "Were data collected in a way that addressed the issue?"),
                Domain("reflexivity", "Reflexivity", "Was the researcher-participant relationship considered?"),
                Domain("ethics", "Ethics", "Were ethical issues taken into consideration?"),
                Domain("analysis", "Analysis", "Was the data analysis sufficiently rigorous?"),
                Domain("findings", "Findings", "Is there a clear statement of findings?"),
            ),
            overall_options=CHECKLIST_OVERALL,
            kind="checklist",
        ),
    )
}

NO_INFO = frozenset({"no_information", "unclear", "cant_tell"})

_KEYWORDS: list[tuple[str, str]] = [
    (r"\bnon[- ]?randomi[sz]ed|quasi[- ]?experiment|before[- ]and[- ]after|interrupted time", "nrsi_rob"),
    (r"\brandomi[sz]ed|\brct\b|cluster[- ]trial|crossover", "rct_rob"),
    (r"\bcohort|case[- ]control|longitudinal|prospective|retrospective", "observational_nos"),
    (r"cross[- ]sectional|survey|prevalence", "cross_sectional_checklist"),
    (r"qualitative|interview|focus group|ethnograph|thematic", "qualitative_checklist"),
]


def get(key: str) -> Framework | None:
    return FRAMEWORKS.get(key)


def suggest(*texts: str | None) -> str | None:
    """Framework suggested for a study, from its extracted design and/or publication type (first keyword match)."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    for pattern, key in _KEYWORDS:
        if re.search(pattern, blob):
            return key
    return None


def suggested_overall(fw: Framework, judgements: dict[str, str | None]) -> tuple[str | None, str]:
    """(suggested overall, explanation). None while any domain is undecided, and always None for checklists."""
    if fw.severity is None:
        return None, "Checklist: no overall score is computed."
    missing = [d.label for d in fw.domains if not judgements.get(d.key)]
    if missing:
        return None, f"Not suggested until every domain is judged ({len(missing)} remaining)."
    worst = max((judgements[d.key] or "" for d in fw.domains), key=fw.severity.index)
    drivers = [d.label for d in fw.domains if judgements[d.key] == worst]
    overall = fw.overall_map[worst]
    return overall, f"Most serious domain judgement '{worst}' in: {', '.join(drivers)}."


def is_serious(fw_key: str, overall: str | None) -> bool:
    """Whether an approved overall judgement means the study's results should be treated with caution."""
    return overall in {"high", "serious", "critical", "high_concern", "major_concerns"}
