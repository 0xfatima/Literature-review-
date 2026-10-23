"""Route-driven authorization guard (Phase 2 definition of done: two users cannot reach each other's data).

The route list is read from the application's own OpenAPI schema (FastAPI >= 0.15x no longer flattens included
routers into app.routes, so walking routes would silently find nothing), so a NEW endpoint is covered automatically:
  * every /api/v1 route except the explicit PUBLIC set must answer 401 without a session;
  * every route with an object id in its path must answer 404 to a second user using the first user's ids
    (404, not 403: existence is not revealed);
  * a conversation id is only valid inside its own project, even for the same owner.
If you add a route with a new kind of path parameter, add a real object for it in `ids` below; the test refuses
unknown parameters rather than silently skipping the route.
"""

import re

import pytest

from .conftest import CSRF

PUBLIC = {("POST", "/api/v1/auth/register"), ("POST", "/api/v1/auth/login")}
# A body that satisfies every current request schema well enough to get PAST validation if authorization
# were missing (authorization dependencies run first in FastAPI, but we do not want to rely on that alone).
BODY = {"title": "x", "topic": "x", "content": "x", "expected_version": 1, "delay_s": 0, "reason": "xyz"}


def api_routes(app):
    for path, ops in sorted(app.openapi()["paths"].items()):
        if path.startswith("/api/v1"):
            for method in sorted(ops):
                if method in ("get", "post", "put", "patch", "delete"):
                    yield method.upper(), path


def fill(path: str, ids: dict[str, str]) -> str:
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in ids:
            pytest.fail(f"route {path} has path parameter {{{name}}} with no fixture object: extend `ids`")
        return ids[name]

    return re.sub(r"\{(\w+)\}", sub, path)


def call(client, method, url):
    return client.request(method, url, json=BODY if method in ("POST", "PATCH", "PUT") else None)


@pytest.fixture
def world(user_client):
    a, _, _ = user_client()
    b, _, _ = user_client()
    p1 = a.post("/api/v1/projects", json={"title": "A1", "topic": "private"}).json()["id"]
    p2 = a.post("/api/v1/projects", json={"title": "A2", "topic": "private"}).json()["id"]
    c1 = a.post(f"/api/v1/projects/{p1}/conversations", json={}).json()["id"]
    a.post(f"/api/v1/projects/{p1}/conversations/{c1}/messages", json={"content": "private message"})
    job = a.post("/api/v1/jobs/ping", json={"project_id": p1, "delay_s": 41}).json()["id"]
    assert a.post(f"/api/v1/projects/{p1}/workflow").status_code == 202  # Phase 3: workflow run + protocol draft
    version = a.get(f"/api/v1/projects/{p1}/protocol").json()["draft"]["id"]
    return a, b, {"project_id": p1, "conversation_id": c1, "job_id": job, "version_id": version}, p2


def test_route_inventory_is_not_vacuous(app):
    routes = list(api_routes(app))
    scoped = [r for r in routes if "{" in r[1]]
    assert len(routes) >= 27 and len(scoped) >= 19, routes


def test_every_non_public_route_requires_a_session(app, make_client, world):
    _, _, ids, _ = world
    anon = make_client()
    for method, path in api_routes(app):
        if (method, path) in PUBLIC:
            continue
        r = call(anon, method, fill(path, ids))
        assert r.status_code == 401, f"{method} {path} -> {r.status_code} without a session"
        assert r.json()["error"]["code"] == "unauthorized"


def test_other_user_gets_404_on_every_object_route(app, world):
    a, b, ids, _ = world
    checked = 0
    for method, path in api_routes(app):
        if "{" not in path:
            continue
        url = fill(path, ids)
        r = call(b, method, url)
        assert r.status_code == 404, f"{method} {path}: other user got {r.status_code}: {r.text[:200]}"
        assert "private" not in r.text
        checked += 1
    assert checked >= 10
    # ...and nothing was changed by those attempts: the owner still sees everything intact.
    p = ids["project_id"]
    assert a.get(f"/api/v1/projects/{p}").json()["title"] == "A1"
    msgs = a.get(f"/api/v1/projects/{p}/conversations/{ids['conversation_id']}/messages").json()["items"]
    assert [m["content"] for m in msgs] == ["private message"]


def test_owner_can_read_every_object_route_used_above(app, world):
    """Guards against a vacuous pass: the ids used in the 404 test are real and readable by their owner."""
    a, _, ids, _ = world
    for method, path in api_routes(app):
        if method == "GET" and "{" in path:
            r = a.get(fill(path, ids))
            assert r.status_code == 200, f"owner GET {path} -> {r.status_code}"


def test_conversation_is_not_reachable_through_another_project_of_the_same_owner(app, world):
    a, _, ids, p2 = world
    for method, path in api_routes(app):
        if "{conversation_id}" in path or "{version_id}" in path:
            r = call(a, method, fill(path, {**ids, "project_id": p2}))
            assert r.status_code == 404, f"{method} {path} via other project -> {r.status_code}"


def test_other_users_lists_never_include_foreign_objects(world):
    _, b, ids, _ = world
    assert b.get("/api/v1/projects?include_archived=true").json()["total"] == 0
    assert b.get("/api/v1/jobs").json()["items"] == []
    assert CSRF  # lists are GETs; CSRF headers are present on every test client anyway
