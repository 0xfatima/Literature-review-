import uuid




def mk(c, title="Review A", topic="Effect of X on Y"):
    r = c.post("/api/v1/projects", json={"title": title, "topic": topic})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_list_reopen(user_client):
    c, _, _ = user_client()
    p = mk(c)
    assert p["status"] == "setup" and p["row_version"] == 1 and p["topic_original"] == "Effect of X on Y"
    assert c.get(f"/api/v1/projects/{p['id']}").json()["id"] == p["id"]
    lst = c.get("/api/v1/projects").json()
    assert lst["total"] == 1 and lst["items"][0]["id"] == p["id"]


def test_rename_archive_unarchive_and_topic_is_immutable(user_client):
    c, _, _ = user_client()
    p = mk(c)
    r = c.patch(f"/api/v1/projects/{p['id']}", json={"expected_version": 1, "title": "Renamed",
                                                     "topic_original": "tampered"})
    assert r.status_code == 200 and r.json()["title"] == "Renamed" and r.json()["row_version"] == 2
    assert r.json()["topic_original"] == "Effect of X on Y"
    r = c.patch(f"/api/v1/projects/{p['id']}", json={"expected_version": 2, "archived": True})
    assert r.json()["archived_at"] is not None
    assert c.get("/api/v1/projects").json()["total"] == 0
    assert c.get("/api/v1/projects?include_archived=true").json()["total"] == 1
    r = c.patch(f"/api/v1/projects/{p['id']}", json={"expected_version": 3, "archived": False})
    assert r.json()["archived_at"] is None


def test_stale_version_conflicts(user_client):
    c, _, _ = user_client()
    p = mk(c)
    assert c.patch(f"/api/v1/projects/{p['id']}", json={"expected_version": 1, "title": "One"}).status_code == 200
    r = c.patch(f"/api/v1/projects/{p['id']}", json={"expected_version": 1, "title": "Two"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "version_conflict"
    assert c.get(f"/api/v1/projects/{p['id']}").json()["title"] == "One"


def test_validation(user_client):
    c, _, _ = user_client()
    assert c.post("/api/v1/projects", json={"title": "   ", "topic": "t"}).status_code == 422
    assert c.post("/api/v1/projects", json={"title": "x" * 201, "topic": "t"}).status_code == 422
    assert c.get("/api/v1/projects/not-a-uuid").status_code == 422


def test_delete_removes_project(user_client):
    c, _, _ = user_client()
    p = mk(c)
    assert c.delete(f"/api/v1/projects/{p['id']}").status_code == 204
    assert c.get(f"/api/v1/projects/{p['id']}").status_code == 404


def test_pagination(user_client):
    c, _, _ = user_client()
    for i in range(5):
        mk(c, title=f"P{i}")
    page = c.get("/api/v1/projects?limit=2&offset=2").json()
    assert page["total"] == 5 and len(page["items"]) == 2


# ---------------- authorization: user B must not reach user A's resources -----------------

def test_other_user_gets_404_on_every_project_route(user_client):
    a, _, _ = user_client()
    b, _, _ = user_client()
    pid = mk(a)["id"]
    routes = [("get", f"/api/v1/projects/{pid}", None),
              ("patch", f"/api/v1/projects/{pid}", {"expected_version": 1, "title": "hijack"}),
              ("delete", f"/api/v1/projects/{pid}", None)]
    for method, url, body in routes:
        r = getattr(b, method)(url, **({"json": body} if body else {}))
        assert r.status_code == 404, (method, url, r.status_code)
        assert r.json()["error"]["code"] == "not_found"
    # ...and nothing was changed or deleted by those attempts
    mine = a.get(f"/api/v1/projects/{pid}").json()
    assert mine["title"] == "Review A" and mine["row_version"] == 1


def test_foreign_and_nonexistent_projects_are_indistinguishable(user_client):
    a, _, _ = user_client()
    b, _, _ = user_client()
    pid = mk(a)["id"]
    foreign = b.get(f"/api/v1/projects/{pid}")
    missing = b.get(f"/api/v1/projects/{uuid.uuid4()}")
    assert foreign.status_code == missing.status_code == 404
    f, m = foreign.json()["error"], missing.json()["error"]
    assert (f["code"], f["message"]) == (m["code"], m["message"])


def test_list_only_contains_own_projects(user_client):
    a, _, _ = user_client()
    b, _, _ = user_client()
    mk(a, "A-only")
    mk(b, "B-only")
    titles_a = {p["title"] for p in a.get("/api/v1/projects").json()["items"]}
    assert titles_a == {"A-only"}


def test_unauthenticated_requests_are_rejected_everywhere(make_client, user_client):
    a, _, _ = user_client()
    pid = mk(a)["id"]
    anon = make_client()
    for method, url in [("get", "/api/v1/projects"), ("post", "/api/v1/projects"),
                        ("get", f"/api/v1/projects/{pid}"), ("patch", f"/api/v1/projects/{pid}"),
                        ("delete", f"/api/v1/projects/{pid}"), ("post", "/api/v1/jobs/ping"),
                        ("get", f"/api/v1/jobs/{uuid.uuid4()}"), ("post", f"/api/v1/jobs/{uuid.uuid4()}/cancel"),
                        ("post", "/api/v1/auth/logout")]:
        r = getattr(anon, method)(url, json={}) if method in ("post", "patch") else getattr(anon, method)(url)
        assert r.status_code == 401, (method, url, r.status_code)


def test_sql_metacharacters_are_data_not_code(user_client):
    c, _, _ = user_client()
    nasty = "x'); DROP TABLE projects;--"
    p = mk(c, title=nasty, topic=nasty)
    assert c.get(f"/api/v1/projects/{p['id']}").json()["title"] == nasty
    assert c.get("/api/v1/projects").json()["total"] == 1


def test_cannot_attach_job_to_foreign_project(user_client):
    a, _, _ = user_client()
    b, _, _ = user_client()
    pid = mk(a)["id"]
    assert b.post("/api/v1/jobs/ping", json={"project_id": pid}).status_code == 404
