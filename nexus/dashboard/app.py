"""Flask dashboard app for Nexus."""

import os
import sys
import time
import platform
from pathlib import Path

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False

try:
    from flask import Flask, jsonify, request
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

from ..dashboard.api import get_api

__all__ = ["create_app"]


def create_app() -> "Flask":
    """Create and configure the Flask dashboard app."""
    if not FLASK_AVAILABLE:
        raise ImportError("Flask not installed. Run: pip install flask")

    app = Flask(__name__, template_folder=None)

    @app.route("/")
    def index():
        api = get_api()
        vitals = _get_vitals()
        status = api.get_status()
        skills = api.get_skills()
        providers = api.get_providers()
        automation = api.get_automation_status()
        tools = api.get_tools()
        return jsonify({
            "status": status,
            "vitals": vitals,
            "skills": skills,
            "providers": providers,
            "automation": automation,
            "tools": tools,
        })

    @app.route("/api/status")
    def api_status():
        return jsonify(get_api().get_status())

    @app.route("/api/providers", methods=["GET", "POST"])
    def api_providers():
        api = get_api()
        if request.method == "POST":
            return jsonify(api.add_provider(request.json or {}))
        return jsonify(api.get_providers())

    @app.route("/api/skills")
    def api_skills():
        return jsonify(get_api().get_skills())

    @app.route("/api/skills/<name>/activate", methods=["POST"])
    def api_skill_activate(name):
        return jsonify(get_api().activate_skill(name))

    @app.route("/api/memory/search", methods=["POST"])
    def api_memory_search():
        data = request.json or {}
        return jsonify(get_api().search_memory(data.get("query", ""), data.get("limit", 10)))

    @app.route("/api/memory/store", methods=["POST"])
    def api_memory_store():
        data = request.json or {}
        return jsonify(get_api().store_memory(data.get("content", ""), data.get("metadata")))

    @app.route("/api/facts", methods=["GET", "POST"])
    def api_facts():
        api = get_api()
        if request.method == "POST":
            data = request.json or {}
            return jsonify(api.add_fact(data.get("key", ""), data.get("value"), data.get("category", "general")))
        return jsonify(api.get_facts())

    @app.route("/api/sessions")
    def api_sessions():
        return jsonify(get_api().list_sessions())

    @app.route("/api/agent/stats")
    def api_agent_stats():
        return jsonify(get_api().get_agent_stats())

    @app.route("/api/tools")
    def api_tools():
        return jsonify(get_api().get_tools())

    @app.route("/api/automation/status")
    def api_automation_status():
        return jsonify(get_api().get_automation_status())

    @app.route("/api/automation/execute", methods=["POST"])
    def api_automation_execute():
        data = request.json or {}
        tool = data.get("tool")
        params = data.get("params", {})
        return jsonify(get_api().run_automation_tool(tool, params))

    @app.route("/api/vitals")
    def api_vitals():
        return jsonify(_get_vitals())

    @app.route("/api/execute", methods=["POST"])
    async def api_execute():
        api = get_api()
        data = request.json or {}
        task = data.get("task", "")
        if not task:
            return jsonify({"error": "No task provided"})
        result = await api.run_agent_task(task)
        return jsonify(result)

    return app


def _get_vitals() -> dict:
    vitals = {}
    if not PSUTIL_AVAILABLE:
        vitals["disk"] = "?"
        vitals["cpu"] = "?"
        vitals["memory"] = "?"
        return vitals
    try:
        usage = psutil.disk_usage("/")
        vitals["disk"] = f"{usage.percent}%"
    except Exception:
        vitals["disk"] = "?"
    try:
        vitals["cpu"] = f"{psutil.cpu_percent(interval=0.1)}%"
    except Exception:
        vitals["cpu"] = "?"
    try:
        mem = psutil.virtual_memory()
        vitals["memory"] = f"{mem.percent}%"
    except Exception:
        vitals["memory"] = "?"
    return vitals
