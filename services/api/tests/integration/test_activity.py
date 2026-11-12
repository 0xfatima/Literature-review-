"""One review step at a time per project, and pausing / resuming it (real PostgreSQL)."""

from .support import OPEN_ACCESS, run_relevance_jobs, run_search_jobs, searched, start_search


def activity(c, pid):
    r = c.get(f"/api/v1/projects/{pid}/activity")
    assert r.status_code == 200, r.text
    return r.json()


def test_only_one_step_runs_at_a_time_and_a_paused_step_resumes(user_client, dispatcher):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    assert activity(c, pid)["running"] is None

    r = c.post(f"/api/v1/projects/{pid}/screening/relevance", json={"use_llm": False})
    assert r.status_code == 202, r.text
    a = activity(c, pid)
    assert a["running"]["type"] == "relevance.assess" and a["running_label"] == "AI screening" and a["can_pause"]

    blocked = c.post(f"/api/v1/projects/{pid}/search-runs", json={"providers": OPEN_ACCESS})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "step_running"

    paused = c.post(f"/api/v1/projects/{pid}/activity/pause").json()
    assert paused["running"] is None and paused["paused"]["type"] == "relevance.assess"
    assert c.post(f"/api/v1/projects/{pid}/activity/pause").status_code == 409  # nothing running now

    resumed = c.post(f"/api/v1/projects/{pid}/activity/resume").json()
    assert resumed["running"]["type"] == "relevance.assess" and resumed["paused"] is None
    assert run_relevance_jobs(dispatcher) == ["ignored", "ok"]  # the paused job is skipped, its continuation runs
    assert activity(c, pid)["running"] is None and activity(c, pid)["paused"] is None
    assert c.post(f"/api/v1/projects/{pid}/activity/resume").status_code == 409


def test_a_paused_search_continues_from_where_it_stopped(user_client, dispatcher):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    run = start_search(c, pid, providers=OPEN_ACCESS)
    assert activity(c, pid)["running_label"] == "The search"
    c.post(f"/api/v1/projects/{pid}/activity/pause")
    assert c.get(f"/api/v1/projects/{pid}/search-runs/{run['id']}").json()["status"] == "cancelled"
    c.post(f"/api/v1/projects/{pid}/activity/resume")
    assert c.get(f"/api/v1/projects/{pid}/search-runs/{run['id']}").json()["status"] == "queued"
    assert run_search_jobs(dispatcher)[-1] == "completed"
    assert activity(c, pid)["running"] is None
