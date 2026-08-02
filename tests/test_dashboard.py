"""Tests for the GIA / Nexus web dashboard.

There was previously zero test coverage here, which is how a broken
top-level import (`AgentRole`/`MultiAgentTeam` imported from the wrong
module) went unnoticed and silently made *every* dashboard API route
return a generic 503. These tests exist to catch that class of bug.
"""

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from nexus.dashboard.app import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


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


def test_api_mcp_install_and_uninstall(client):
    resp = client.post("/api/mcp/install", json={"name": "redis"})
    assert resp.status_code == 200
    result = resp.get_json()
    assert result["status"] == "success"

    resp = client.get("/api/mcp/installed")
    names = [s["name"] for s in resp.get_json()["servers"]]
    assert "redis" in names

    resp = client.post("/api/mcp/uninstall", json={"name": "redis"})
    assert resp.get_json()["status"] == "success"


def test_api_mcp_install_unknown_server(client):
    resp = client.post("/api/mcp/install", json={"name": "definitely-not-a-real-server"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "error"


def test_api_vitals(client):
    resp = client.get("/api/vitals")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "cpu" in data and "disk" in data


def test_api_chat_stream_requires_message(client):
    resp = client.post("/api/chat/stream", json={})
    assert resp.status_code == 400


def test_api_chat_stream_returns_sse(client):
    resp = client.post("/api/chat/stream", json={"message": "hello"})
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    body = resp.get_data(as_text=True)
    # With no provider configured this should still complete gracefully
    # with a "done" event rather than hanging or crashing.
    assert '"type": "done"' in body or '"type":"done"' in body


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
