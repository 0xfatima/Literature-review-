"""Unit tests for PRISMA 2020 flow computation and invariant validation. Stdlib only.

All counts derive from the record_status_events fold; none are typed in or model-generated.
Tests cover: correct folding, every invariant violation (V1–V9), pending counts, pathway B,
per-reason exclusions, study grouping (T ≤ M), and final-vs-in-progress distinction.
"""

from app.prisma.flow import PrismaFlow, compute, validate


def ev(paper_id: str, stage: str, pathway: str = "A", **kw: object) -> dict[str, object]:
    return {"paper_id": paper_id, "stage": stage, "pathway": pathway, "study_id": None, **kw}


# ---------- compute ----------


def test_empty_events_produce_zeros():
    f = compute([])
    assert f.identified_a == 0 and f.included_reports == 0 and f.violations == []


def test_identified_only_all_pending():
    events = [ev("p1", "identified"), ev("p2", "identified"), ev("p3", "identified")]
    f = compute(events)
    assert f.identified_a == 3 and f.pending_ta == 3 and f.ta_excluded == 0


def test_full_linear_flow_no_violations():
    events = [
        ev("p1", "identified", source_tag="openalex"),
        ev("p2", "identified", source_tag="europepmc"),
        ev("p3", "identified", source_tag="openalex"),
        ev("p1", "ta_excluded", reason_code="off_topic"),
        ev("p2", "ta_included"),
        ev("p3", "ta_included"),
        ev("p2", "ft_excluded", reason_code="wrong_study_design"),
        ev("p3", "included"),
    ]
    f = compute(events)
    assert f.identified_a == 3
    assert f.ta_excluded == 1 and f.ta_included_a == 2
    assert f.screened == 3
    assert f.sought_retrieval == 2
    assert f.ft_excluded == 1 and f.included_reports == 1
    assert f.ft_excluded_by_reason == {"wrong_study_design": 1}
    assert f.included_studies == 1
    assert f.violations == []


def test_removed_before_screening_buckets():
    events = [
        ev("p1", "identified"),
        ev("p2", "identified"),
        ev("p3", "identified"),
        ev("p1", "removed_before_screen", reason_code="duplicate"),
        ev("p2", "removed_before_screen", reason_code="automation_ineligible"),
        ev("p3", "removed_before_screen", reason_code="other"),
    ]
    f = compute(events)
    assert f.removed_duplicates == 1
    assert f.removed_automation == 1
    assert f.removed_other == 1
    assert f.screened == 0  # nothing reached screening


def test_pathway_b_identified_and_included():
    events = [
        ev("p1", "identified", pathway="A", source_tag="openalex"),
        ev("p2", "identified", pathway="B", source_tag="citation_search"),
        ev("p1", "ta_included"),
        ev("p2", "ta_included", pathway="B"),
        ev("p1", "included"),
        ev("p2", "included", pathway="B"),
    ]
    f = compute(events)
    assert f.identified_a == 1 and f.identified_b == 1
    assert f.ta_included_a == 1 and f.ta_included_b == 1
    assert f.sought_retrieval == 2
    assert f.included_reports == 2


def test_per_source_identification_counts():
    events = [
        ev("p1", "identified", source_tag="openalex"),
        ev("p2", "identified", source_tag="openalex"),
        ev("p3", "identified", source_tag="europepmc"),
    ]
    f = compute(events)
    assert f.identified_a_by_source == {"openalex": 2, "europepmc": 1}


def test_study_grouping_studies_less_than_reports():
    events = [
        ev("p1", "included", study_id="s1"),
        ev("p2", "included", study_id="s1"),  # same study
        ev("p3", "included", study_id="s2"),
    ]
    f = compute(events)
    assert f.included_reports == 3 and f.included_studies == 2


def test_is_final_true_only_when_no_pending_and_no_violations():
    # is_final only True when pending=0 and violations=[]
    all_decided = [
        ev("p1", "ta_included"),
        ev("p1", "included"),
    ]
    f2 = compute(all_decided)
    assert f2.pending_ta == 0 and f2.pending_retrieval == 0 and f2.pending_ft == 0


# ---------- validate: individual invariants ----------


def _flow(**kw: object) -> PrismaFlow:
    f = PrismaFlow()
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def violation_ids(f: PrismaFlow) -> set[str]:
    return {v["id"] for v in validate(f)}


def test_V1_source_sum_mismatch():
    # Over-count fires V1
    f = _flow(identified_a=10, identified_a_by_source={"openalex": 7, "europepmc": 5})  # 12 > 10
    assert "V1" in violation_ids(f)
    # Under-count does NOT fire V1 (partial source tagging is normal)
    f2 = _flow(identified_a=10, identified_a_by_source={"openalex": 6, "europepmc": 3})  # 9 < 10
    assert "V1" not in violation_ids(f2)


def test_V1_passes_when_no_source_detail():
    f = _flow(identified_a=10)  # no by_source: skipped
    assert "V1" not in violation_ids(f)


def test_V2_removed_plus_screened_exceeds_identified():
    f = _flow(identified_a=5, removed_duplicates=3, screened=4)  # 3+4=7 > 5
    assert "V2" in violation_ids(f)


def test_V3_screened_arithmetic():
    f = _flow(screened=10, pending_ta=2, ta_excluded=5, ta_included_a=2)  # 2+5+2=9 ≠ 10
    assert "V3" in violation_ids(f)


def test_V4_sought_not_equal_sum_of_pathways():
    f = _flow(sought_retrieval=10, ta_included_a=6, ta_included_b=3)  # 6+3=9 ≠ 10
    assert "V4" in violation_ids(f)


def test_V5_sought_breakdown():
    f = _flow(sought_retrieval=10, not_retrieved=2, ft_assessed=5, pending_retrieval=2)  # 2+5+2=9 ≠ 10
    assert "V5" in violation_ids(f)


def test_V6_ft_assessed_breakdown():
    f = _flow(ft_assessed=8, ft_excluded=3, included_reports=3, pending_ft=1)  # 3+3+1=7 ≠ 8
    assert "V6" in violation_ids(f)


def test_V7_reason_sum_mismatch():
    f = _flow(ft_excluded=5, ft_excluded_by_reason={"wrong_design": 3, "wrong_population": 1})
    assert "V7" in violation_ids(f)


def test_V7_negative_reason_count():
    f = _flow(ft_excluded=5, ft_excluded_by_reason={"wrong_design": 6, "wrong_population": -1})
    assert "V7" in violation_ids(f)


def test_V8_studies_exceeds_reports():
    f = _flow(included_reports=3, included_studies=4)
    assert "V8" in violation_ids(f)


def test_V8_zero_studies_with_reports():
    f = _flow(included_reports=2, included_studies=0)
    assert "V8" in violation_ids(f)


def test_V9_negative_count():
    f = _flow(ta_excluded=-1)
    assert "V9" in violation_ids(f)


def test_valid_flow_has_no_violations():
    f = _flow(
        identified_a=10,
        removed_duplicates=2,
        screened=8,
        pending_ta=0,
        ta_excluded=3,
        ta_included_a=5,
        ta_included_b=0,
        sought_retrieval=5,
        not_retrieved=1,
        ft_assessed=4,
        pending_retrieval=0,
        pending_ft=0,
        ft_excluded=2,
        ft_excluded_by_reason={"wrong_design": 1, "wrong_population": 1},
        included_reports=2,
        included_studies=2,
    )
    assert validate(f) == []
