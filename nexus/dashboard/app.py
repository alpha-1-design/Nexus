"""Flask web dashboard for Nexus."""

import json
import logging
import time

from flask import Flask, Response, jsonify, render_template, request, send_from_directory, stream_with_context

app = Flask(__name__, template_folder="templates", static_folder="static")
logger = logging.getLogger(__name__)


def _get_api():
    try:
        from nexus.dashboard.api import get_api
        return get_api()
    except Exception:
        # Log the real cause (import error, etc.) instead of silently
        # swallowing it -- a bad import here previously made every
        # dashboard route return a generic 503 with no diagnostic trail.
        logger.exception("Nexus dashboard API unavailable")
        return None


def _vitals():
    try:
        import psutil
    except ImportError:
        return {"disk": "?", "cpu": "?"}
    out = {}
    try:
        out["disk"] = f"{psutil.disk_usage('/').percent}%"
    except Exception:
        out["disk"] = "?"
    try:
        out["cpu"] = f"{psutil.cpu_percent(interval=0.1)}%"
    except Exception:
        out["cpu"] = "?"
    return out


def _api(f):
    def wrapper(*args, **kwargs):
        api = _get_api()
        if api is None:
            return jsonify({"error": "Nexus API not available"}), 503
        return f(api, *args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper


# ── page ─────────────────────────────────────────────────────

@app.route("/")
def index():
    # The GIA dashboard is a lightweight SPA shell: all data is hydrated
    # client-side via fetch() against the JSON API below, so this route
    # stays instant instead of blocking page load on config/provider/skill
    # discovery.
    return render_template("index.html")


@app.route("/manifest.json")
def manifest():
    return send_from_directory(app.template_folder, "manifest.json", mimetype="application/json")


@app.route("/sw.js")
def service_worker():
    return send_from_directory(app.template_folder, "sw.js", mimetype="application/javascript")


# ── status ───────────────────────────────────────────────────

@app.route("/api/status")
@_api
def api_status(api):
    return jsonify(api.get_status())


@app.route("/api/vitals")
def api_vitals():
    return jsonify(_vitals())


# ── providers ────────────────────────────────────────────────

@app.route("/api/providers", methods=["GET", "POST", "DELETE"])
@_api
def api_providers(api):
    if request.method == "POST":
        return jsonify(api.add_provider(request.json))
    if request.method == "DELETE":
        data = request.json or {}
        return jsonify(api.remove_provider(data.get("name", "")))
    return jsonify(api.get_providers())


@app.route("/api/providers/<name>/activate", methods=["POST"])
@_api
def api_provider_activate(api, name):
    return jsonify(api.set_active_provider(name))


# ── skills ───────────────────────────────────────────────────

@app.route("/api/skills")
@_api
def api_skills(api):
    return jsonify(api.get_skills())


@app.route("/api/skills/<name>/activate", methods=["POST"])
@_api
def api_skill_activate(api, name):
    return jsonify(api.activate_skill(name))


# ── memory ───────────────────────────────────────────────────

@app.route("/api/memory/search", methods=["POST"])
@_api
def api_memory_search(api):
    data = request.json or {}
    return jsonify(api.search_memory(data.get("query", ""), data.get("limit", 10)))


@app.route("/api/memory/store", methods=["POST"])
@_api
def api_memory_store(api):
    data = request.json or {}
    return jsonify(api.store_memory(data.get("content", ""), data.get("metadata")))


@app.route("/api/facts", methods=["GET", "POST"])
@_api
def api_facts(api):
    if request.method == "POST":
        data = request.json or {}
        return jsonify(api.add_fact(data.get("key", ""), data.get("value"), data.get("category", "general")))
    return jsonify(api.get_facts())


# ── sessions ─────────────────────────────────────────────────

@app.route("/api/sessions")
@_api
def api_sessions(api):
    return jsonify(api.list_sessions())


# ── tools ────────────────────────────────────────────────────

@app.route("/api/tools")
@_api
def api_tools(api):
    return jsonify(api.get_tools())


# ── agent ────────────────────────────────────────────────────

@app.route("/api/agent/stats")
@_api
def api_agent_stats(api):
    return jsonify(api.get_agent_stats())


@app.route("/api/agent/list")
@_api
def api_agent_list(api):
    return jsonify(api.list_agents())


@app.route("/api/agent/spawn", methods=["POST"])
@_api
def api_agent_spawn(api):
    data = request.json or {}
    return jsonify(api.spawn_agent(data.get("task", ""), data.get("role", "coder"), data.get("name")))


@app.route("/api/agent/kill", methods=["POST"])
@_api
def api_agent_kill(api):
    data = request.json or {}
    return jsonify(api.kill_agent(data.get("id", "")))


@app.route("/api/execute", methods=["POST"])
async def api_execute():
    api = _get_api()
    if api is None:
        return jsonify({"error": "Nexus API not available"}), 503
    data = request.json or {}
    task = data.get("task", "")
    if not task:
        return jsonify({"error": "No task provided"}), 400
    result = await api.run_agent_task(task)
    return jsonify(result)


# ── chat (streaming) ─────────────────────────────────────────

@app.route("/api/chat/stream", methods=["POST"])
def api_chat_stream():
    """Stream an assistant response token-by-token over SSE.

    Bridges the async agent orchestrator (which drives an async
    provider-streaming loop) into a synchronous Flask SSE generator via a
    thread + queue, so the browser sees incremental `delta` events as the
    model generates, followed by a single `done` event with the final turn
    metadata (tool calls, duration, etc).
    """
    import asyncio
    import queue
    import threading

    api = _get_api()
    if api is None:
        return jsonify({"error": "Nexus API not available"}), 503

    data = request.json or {}
    message = (data.get("message") or data.get("task") or "").strip()
    if not message:
        return jsonify({"error": "No message provided"}), 400

    q: queue.Queue = queue.Queue()
    _sentinel = object()

    def on_chunk(text: str) -> None:
        q.put({"type": "delta", "content": text})

    def worker() -> None:
        try:
            result = asyncio.run(api.run_agent_task(message, stream_callback=on_chunk))
            q.put({"type": "done", "result": result})
        except Exception as e:  # noqa: BLE001
            q.put({"type": "error", "error": str(e)})
        finally:
            q.put(_sentinel)

    threading.Thread(target=worker, daemon=True).start()

    def generate():
        while True:
            item = q.get()
            if item is _sentinel:
                break
            yield f"data: {json.dumps(item)}\n\n"
        yield "event: close\ndata: {}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


# ── mcp marketplace ──────────────────────────────────────────

@app.route("/api/mcp/catalog")
@_api
def api_mcp_catalog(api):
    query = request.args.get("q", "")
    category = request.args.get("category", "")
    return jsonify(api.get_mcp_catalog(query, category))


@app.route("/api/mcp/installed")
@_api
def api_mcp_installed(api):
    return jsonify(api.get_installed_mcp())


@app.route("/api/mcp/install", methods=["POST"])
@_api
def api_mcp_install(api):
    data = request.json or {}
    return jsonify(api.install_mcp(data.get("name", "")))


@app.route("/api/mcp/uninstall", methods=["POST"])
@_api
def api_mcp_uninstall(api):
    data = request.json or {}
    return jsonify(api.uninstall_mcp(data.get("name", "")))


# ── projects ─────────────────────────────────────────────────

@app.route("/api/projects")
@_api
def api_projects(api):
    root = request.args.get("root")
    return jsonify(api.list_projects(root))


@app.route("/api/projects/tree")
@_api
def api_project_tree(api):
    path = request.args.get("path", ".")
    depth = int(request.args.get("depth", 4))
    return jsonify(api.get_project_tree(path, depth))


@app.route("/api/projects/read")
@_api
def api_project_read(api):
    file_path = request.args.get("path", "")
    return jsonify(api.read_project_file(file_path))


@app.route("/api/projects/write", methods=["POST"])
@_api
def api_project_write(api):
    data = request.json or {}
    return jsonify(api.write_project_file(data.get("path", ""), data.get("content", "")))


# ── settings ─────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET", "POST"])
@_api
def api_settings(api):
    if request.method == "POST":
        return jsonify(api.update_settings(request.json or {}))
    return jsonify(api.get_settings())


# ── automation ───────────────────────────────────────────────

@app.route("/api/automation/status")
@_api
def api_automation_status(api):
    return jsonify(api.get_automation_status())


@app.route("/api/automation/execute", methods=["POST"])
@_api
def api_automation_execute(api):
    data = request.json or {}
    return jsonify(api.run_automation_tool(data.get("tool"), data.get("params", {})))


# ── SSE real-time events ─────────────────────────────────────

@app.route("/api/events")
def api_events():
    def generate():
        while True:
            data = {"vitals": _vitals(), "ts": time.time()}
            yield f"data: {json.dumps(data)}\n\n"
            time.sleep(2)
    return Response(stream_with_context(generate()), mimetype="text/event-stream")
