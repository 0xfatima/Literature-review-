"""Full-text screening queue, 'not retrieved', study grouping, and PRISMA counts after full-text outcomes."""

from __future__ import annotations

from .test_prisma_extraction import ft_decide, get_flow, searched, ta_decide


def _papers(c, pid):
    return [p["id"] for p in c.get(f"/api/v1/projects/{pid}/papers?limit=100").json()["items"]]


def test_flow_stays_consistent_after_full_text_outcomes(user_client, dispatcher):
    """Regression: a full-text outcome superseded the record's 'ta_included' event, so the record dropped out of
    'sought for retrieval' and reappeared as awaiting title/abstract screening (PRISMA check V5 failed)."""
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    p1, p2, p3, p4 = _papers(c, pid)[:4]
    for p in (p1, p2, p3):
        ta_decide(c, pid, p, "include")
    ta_decide(c, pid, p4, "exclude", reason_code="off_topic")
    before = get_flow(c, pid)
    assert before["ta_included_a"] == 3 and before["pending_retrieval"] == 3

    ft_decide(c, pid, p1, "include")
    ft_decide(c, pid, p2, "exclude", reason_code="wrong_study_design")
    assert (
        c.post(f"/api/v1/projects/{pid}/prisma/retrieval/{p3}", json={"note": "Library has no access"}).status_code
        == 200
    )

    f = get_flow(c, pid)
    assert f["violations"] == []
    assert f["ta_included_a"] == 3 and f["sought_retrieval"] == 3
    assert f["pending_ta"] == before["pending_ta"]  # nothing fell back into title/abstract screening
    assert (f["included_reports"], f["ft_excluded"], f["not_retrieved"], f["pending_retrieval"]) == (1, 1, 1, 0)
    assert f["ft_excluded_by_reason"] == {"wrong_study_design": 1}
    assert f["sought_retrieval"] == f["not_retrieved"] + f["ft_assessed"] + f["pending_retrieval"]

    # a correction at full text keeps the flow consistent too
    ft_decide(c, pid, p2, "include")
    f = get_flow(c, pid)
    assert f["violations"] == [] and f["included_reports"] == 2 and f["ft_excluded"] == 0


def test_full_text_queue_lists_only_records_past_title_abstract(user_client, dispatcher):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    p1, p2, p3, p4 = _papers(c, pid)[:4]
    for p in (p1, p2, p3):
        ta_decide(c, pid, p, "include")
    ta_decide(c, pid, p4, "exclude", reason_code="off_topic")
    ft_decide(c, pid, p1, "exclude", reason_code="wrong_population", note="Adults only")
    ft_decide(c, pid, p2, "uncertain")

    q = c.get(f"/api/v1/projects/{pid}/prisma/ft-queue").json()
    by_id = {i["id"]: i for i in q["items"]}
    assert set(by_id) == {p1, p2, p3}  # the title/abstract exclusion is not in the queue
    assert by_id[p1]["status"] == "excluded" and by_id[p1]["reason_code"] == "wrong_population"
    assert by_id[p1]["note"] == "Adults only"
    assert by_id[p2]["status"] == "uncertain" and by_id[p2]["row_version"] == 1
    assert by_id[p3]["status"] == "awaiting"
    assert q["counts"] == {"awaiting": 1, "uncertain": 1, "included": 0, "excluded": 1, "not_retrieved": 0}

    only = c.get(f"/api/v1/projects/{pid}/prisma/ft-queue?status=awaiting").json()["items"]
    assert [i["id"] for i in only] == [p3]
    assert c.get(f"/api/v1/projects/{pid}/prisma/ft-queue?status=bogus").status_code == 422


def test_not_retrieved_can_be_undone_and_is_audited(user_client, dispatcher):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    p1, p2 = _papers(c, pid)[:2]
    ta_decide(c, pid, p1, "include")
    url = f"/api/v1/projects/{pid}/prisma/retrieval/{p1}"

    assert c.post(f"/api/v1/projects/{pid}/prisma/retrieval/{p2}", json={}).status_code == 409  # not past T/A
    assert c.post(url, json={"note": "Paywalled; ILL request refused"}).json()["status"] == "not_retrieved"
    assert c.post(url, json={}).status_code == 409  # already marked
    q = {i["id"]: i for i in c.get(f"/api/v1/projects/{pid}/prisma/ft-queue").json()["items"]}
    assert q[p1]["status"] == "not_retrieved" and q[p1]["note"] == "Paywalled; ILL request refused"
    assert get_flow(c, pid)["not_retrieved"] == 1

    assert c.post(url, json={"retrieved": True}).json()["status"] == "awaiting"
    f = get_flow(c, pid)
    assert f["not_retrieved"] == 0 and f["pending_retrieval"] == 1 and f["violations"] == []

    actions = {
        e["action"] for e in c.get(f"/api/v1/projects/{pid}/audit-events", params={"limit": 100}).json()["items"]
    }
    assert {"full_text.not_retrieved", "full_text.retrieval_restored"} <= actions


def test_studies_list_group_and_ungroup(user_client, dispatcher):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    p1, p2, p3 = _papers(c, pid)[:3]
    for p in (p1, p2, p3):
        ta_decide(c, pid, p, "include")
    ft_decide(c, pid, p1, "include")
    ft_decide(c, pid, p2, "include")

    base = f"/api/v1/projects/{pid}/studies"
    # only reports included at full text can be grouped
    assert c.post(base, json={"paper_ids": [p1, p3]}).status_code == 422
    s = c.get(base).json()
    assert s["included_reports"] == 2 and s["included_studies"] == 2 and s["studies"] == []

    study = c.post(base, json={"paper_ids": [p1, p2], "short_name": "Smith 2020"}).json()
    s = c.get(base).json()
    assert s["included_studies"] == 1 and s["ungrouped_reports"] == []
    assert {r["id"] for r in s["studies"][0]["reports"]} == {p1, p2}
    assert get_flow(c, pid)["included_studies"] == 1
    assert c.post(base, json={"paper_ids": [p1]}).status_code == 409  # already grouped

    assert c.delete(f"{base}/{study['id']}").status_code == 204
    s = c.get(base).json()
    assert s["studies"] == [] and s["included_studies"] == 2
    assert get_flow(c, pid)["included_studies"] == 2
    assert c.delete(f"{base}/{study['id']}").status_code == 404


def test_full_text_routes_are_private(user_client, dispatcher):
    owner, _, _ = user_client()
    other, _, _ = user_client()
    pid, _ = searched(owner, dispatcher)
    p1 = _papers(owner, pid)[0]
    ta_decide(owner, pid, p1, "include")
    assert other.get(f"/api/v1/projects/{pid}/prisma/ft-queue").status_code == 404
    assert other.post(f"/api/v1/projects/{pid}/prisma/retrieval/{p1}", json={}).status_code == 404
    assert other.get(f"/api/v1/projects/{pid}/studies").status_code == 404
