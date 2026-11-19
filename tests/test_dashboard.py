"""Tests for the GIA / Nexus web dashboard.

There was previously zero test coverage here, which is how a broken
top-level import (`AgentRole`/`MultiAgentTeam` imported from the wrong
module) went unnoticed and silently made *every* dashboard API route
return a generic 503. These tests exist to catch that class of bug.
"""

import pytest

TEST_TOKEN = "dashboard-test-token"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Dashboard test client with isolated HOME and a fixed auth token.

    Mutating routes (POST/PUT/DELETE) require the X-Nexus-Token header
    since the dashboard-auth hardening; tests that exercise them pass
    ``headers=auth_headers``.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NEXUS_DASHBOARD_TOKEN", TEST_TOKEN)
    import nexus.dashboard.app as dash_app

    monkeypatch.setattr(dash_app, "_auth", None)

    from nexus.dashboard.app import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"X-Nexus-Token": TEST_TOKEN}


def test_nexus_api_imports_cleanly():
    """Regression test: nexus.dashboard.api must import without error.

    A previous bug imported AgentRole/MultiAgentTeam from the `nexus.agent`
    package (which doesn't export them) instead of the `nexus.agents`
    module, breaking every route that depends on the API layer.
    """
    from nexus.dashboard.api import get_api

    api = get_api()
    assert api is not None


def test_index_page_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"GIA" in resp.data


def test_manifest_and_static_assets(client):
    assert client.get("/manifest.json").status_code == 200
    assert client.get("/static/css/style.css").status_code == 200
    assert client.get("/static/js/app.js").status_code == 200


def test_api_status_available(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "providers" in data
    assert "skills" in data


def test_api_skills_returns_bundled_library(client):
    resp = client.get("/api/skills")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["skills"]) > 100  # bundled community library


def test_api_mcp_catalog(client):
    resp = client.get("/api/mcp/catalog")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] > 0
    assert "servers" in data and "categories" in data


def test_api_mcp_catalog_category_filter(client):
    resp = client.get("/api/mcp/catalog?category=database")
    assert resp.status_code == 200
    data = resp.get_json()
    assert all(s["category"] == "database" for s in data["servers"])


def test_api_mcp_install_and_uninstall(client, auth_headers):
    resp = client.post("/api/mcp/install", json={"name": "redis"}, headers=auth_headers)
    assert resp.status_code == 200
    result = resp.get_json()
    assert result["status"] == "success"

    resp = client.get("/api/mcp/installed")
    names = [s["name"] for s in resp.get_json()["servers"]]
    assert "redis" in names

    resp = client.post("/api/mcp/uninstall", json={"name": "redis"}, headers=auth_headers)
    assert resp.get_json()["status"] == "success"


def test_api_mcp_install_unknown_server(client, auth_headers):
    resp = client.post(
        "/api/mcp/install",
        json={"name": "definitely-not-a-real-server"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "error"


def test_api_vitals(client):
    resp = client.get("/api/vitals")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "cpu" in data and "disk" in data


def test_api_chat_stream_requires_message(client, auth_headers):
    resp = client.post("/api/chat/stream", json={}, headers=auth_headers)
    assert resp.status_code == 400


def test_api_chat_stream_returns_sse(client, auth_headers):
    resp = client.post("/api/chat/stream", json={"message": "hello"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    body = resp.get_data(as_text=True)
    # With no provider configured this should still complete gracefully
    # with a "done" event rather than hanging or crashing.
    assert '"type": "done"' in body or '"type":"done"' in body


def test_api_chat_stream_without_token_is_401(client):
    resp = client.post("/api/chat/stream", json={"message": "hello"})
    assert resp.status_code == 401


def test_api_agent_list(client):
    resp = client.get("/api/agent/list")
    assert resp.status_code == 200
    assert "agents" in resp.get_json()


def test_api_sessions(client):
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    assert "sessions" in resp.get_json()


def test_api_providers(client):
    resp = client.get("/api/providers")
    assert resp.status_code == 200


def test_vitals_degrades_gracefully_without_psutil(monkeypatch):
    """Regression: _vitals() used to `import psutil` unguarded at the top
    of the function, so a missing/unbuildable psutil (a real possibility
    on non-glibc platforms like Termux) would hard-crash the endpoint
    instead of degrading to '?' like the rest of the metrics already did.
    """
    import builtins
    from nexus.dashboard.app import _vitals

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("simulated: psutil not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = _vitals()
    assert result == {"disk": "?", "cpu": "?"}


# ── /api/stats (agent-intelligence stats strip) ──────────────


@pytest.fixture
def fresh_api(monkeypatch, tmp_path):
    """A clean NexusAPI singleton (no orchestrator) + a hermetic in-memory
    stub for the memory backend.

    The real Memory binds to an import-time HOME snapshot, so letting the
    dashboard read the real backend would leak the sandbox's live facts
    into test assertions (and vice versa). The stub mirrors the surface
    get_stats()/add_fact() use: ``_facts`` and ``sessions_dir``.
    """
    import types

    import nexus.dashboard.api as api_mod

    class StubMemory:
        def __init__(self):
            self._facts = {}
            self.sessions_dir = tmp_path / "sessions"
            self.sessions_dir.mkdir(parents=True, exist_ok=True)

        def add_fact(self, key, value, category="general"):
            self._facts[key] = types.SimpleNamespace(key=key, value=value, category=category)

    stub = StubMemory()
    monkeypatch.setattr(api_mod, "_api", None)
    monkeypatch.setattr(api_mod, "get_memory", lambda: stub)
    return api_mod.get_api()


def test_api_stats_shape(client, fresh_api):
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    for key in ("top_skills", "total_skill_loads", "memory", "provider", "agent"):
        assert key in data

    # Skill usage: no orchestrator has run yet in a fresh API → null.
    assert data["top_skills"] is None
    assert data["total_skill_loads"] is None
    assert data["agent"] is None

    # Memory counts are ints in an isolated HOME (empty store → 0).
    assert isinstance(data["memory"], dict)
    assert data["memory"]["facts"] == 0
    assert data["memory"]["sessions"] == 0

    # Provider identity: name + model keys, model may be null.
    assert isinstance(data["provider"], dict)
    assert "name" in data["provider"] and "model" in data["provider"]


def test_api_stats_top_skills_from_orchestrator(client, fresh_api):
    class StubOrchestrator:
        turn_count = 4
        tool_call_count = 7

        def __init__(self):
            self._skill_usage = {"commit": 3, "debug": 2, "review-pr": 1}

        def get_skill_usage(self):
            return dict(self._skill_usage)

    fresh_api._orchestrator = StubOrchestrator()
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total_skill_loads"] == 6
    assert data["top_skills"] == [
        {"name": "commit", "count": 3},
        {"name": "debug", "count": 2},
        {"name": "review-pr", "count": 1},
    ]
    assert data["agent"] == {"turns": 4, "tool_calls": 7}


def test_api_stats_counts_stored_fact_and_sessions(client, auth_headers, fresh_api, tmp_path):
    resp = client.post(
        "/api/facts",
        json={"key": "dashboard_stats_test", "value": "python"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    # A session file in the (stubbed) sessions dir must be counted too.
    (tmp_path / "sessions" / "abc123.json").write_text("{}")
    resp = client.get("/api/stats")
    memory = resp.get_json()["memory"]
    assert memory["facts"] == 1
    assert memory["sessions"] == 1


def test_api_stats_graceful_when_memory_backend_absent(client, monkeypatch):
    """A dead memory backend yields a null section, never a 500."""
    import nexus.dashboard.api as api_mod

    def boom():
        raise RuntimeError("simulated: memory backend unavailable")

    monkeypatch.setattr(api_mod, "get_memory", boom)
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["memory"] is None
    # Unrelated sections still populate.
    assert "provider" in data


def test_api_stats_graceful_when_provider_manager_absent(client, monkeypatch):
    import nexus.dashboard.api as api_mod

    def boom():
        raise RuntimeError("simulated: provider manager unavailable")

    monkeypatch.setattr(api_mod, "get_manager", boom)
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    assert resp.get_json()["provider"] is None


def test_api_stats_graceful_when_orchestrator_stats_raise(client, fresh_api):
    class BrokenOrchestrator:
        turn_count = 0
        tool_call_count = 0

        def get_skill_usage(self):
            raise RuntimeError("simulated: usage tracking broken")

    fresh_api._orchestrator = BrokenOrchestrator()
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["top_skills"] is None
    assert data["total_skill_loads"] is None


def test_api_stats_open_in_token_mode(client):
    """GET /api/stats is a read: open without a token in standard mode."""
    resp = client.get("/api/stats")
    assert resp.status_code == 200


def test_api_stats_locked_in_strict_mode(client, monkeypatch):
    """Strict mode locks every /api route, GET included (mirrors the
    dashboard-auth contract tested for the other routes)."""
    monkeypatch.setenv("NEXUS_DASHBOARD_AUTH", "strict")
    resp = client.get("/api/stats")
    assert resp.status_code == 401
    resp = client.get("/api/stats", headers={"X-Nexus-Token": TEST_TOKEN})
    assert resp.status_code == 200


# ── /api/context (context-usage chip) ────────────────────────


def test_api_context_without_orchestrator(client, fresh_api):
    """Before the first chat turn there is no orchestrator to measure."""
    resp = client.get("/api/context")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["available"] is False
    assert data["ratio"] is None
    assert data["tokens"] is None


def test_api_context_with_orchestrator(client, fresh_api):
    class StubOrchestrator:
        def context_usage(self):
            return {
                "tokens": 42000,
                "window": 128000,
                "ratio": 42000 / 128000,
                "messages": 9,
            }

    fresh_api._orchestrator = StubOrchestrator()
    resp = client.get("/api/context")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["available"] is True
    assert abs(data["ratio"] - 42000 / 128000) < 1e-9
    assert data["tokens"] == 42000
    assert data["window"] == 128000
    assert data["messages"] == 9


def test_api_context_graceful_when_usage_raises(client, fresh_api):
    class BrokenOrchestrator:
        def context_usage(self):
            raise RuntimeError("simulated: context estimation broken")

    fresh_api._orchestrator = BrokenOrchestrator()
    resp = client.get("/api/context")
    assert resp.status_code == 200
    assert resp.get_json()["available"] is False


# ── frontend smoke (stats strip + ctx chip ship with the shell) ──


def test_index_page_includes_stats_strip_and_ctx_chip(client):
    resp = client.get("/")
    body = resp.get_data(as_text=True)
    assert "stats-strip" in body
    assert "ctx-chip" in body
    # untouched shell still renders the GIA brand
    assert b"GIA" in resp.data


def test_app_js_serves_stats_and_context_refresh(client):
    resp = client.get("/static/js/app.js")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "/api/stats" in body
    assert "/api/context" in body
    assert "60000" in body  # 60s refresh timer
    # the token header pattern is reused for the new calls too
    assert "apiFetch" in body
