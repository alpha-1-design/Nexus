/* GIA — Nexus Dashboard front-end.
 * No build step, no framework: plain fetch + DOM, matching the rest of
 * Nexus's lightweight-dependency philosophy.
 */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const escapeHtml = (s) =>
    s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /** Very small markdown-ish renderer: fenced code blocks + inline code. */
  function renderContent(text) {
    const parts = text.split(/```(\w*)\n?([\s\S]*?)```/g);
    let html = "";
    for (let i = 0; i < parts.length; i += 3) {
      const plain = parts[i] || "";
      html += escapeHtml(plain).replace(/`([^`]+)`/g, "<code>$1</code>");
      if (i + 2 < parts.length) {
        const lang = parts[i + 1];
        const code = parts[i + 2];
        html += `<pre><code class="lang-${escapeHtml(lang)}">${escapeHtml(code)}</code></pre>`;
      }
    }
    return html;
  }

  // ── state ──────────────────────────────────────────────────
  const state = {
    sessions: [],
    currentMessages: [],
    streaming: false,
  };

  // ═══════════ SIDEBAR / VIEW SWITCHING ═══════════

  function initSidebar() {
    const collapseBtn = $("#sidebar-collapse-btn");
    const sidebar = $("#sidebar");
    collapseBtn.addEventListener("click", () => sidebar.classList.toggle("collapsed"));

    $("#mobile-menu-btn").addEventListener("click", () => sidebar.classList.toggle("mobile-open"));

    $$(".view-nav-item").forEach((btn) => {
      btn.addEventListener("click", () => switchView(btn.dataset.view));
    });

    $("#new-chat-btn").addEventListener("click", () => startNewChat());
  }

  function switchView(view) {
    $$(".view-nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    $$(".view").forEach((v) => v.classList.remove("active"));
    const target = document.getElementById(`view-${view}`);
    if (target) target.classList.add("active");
    document.getElementById("sidebar").classList.remove("mobile-open");

    if (view === "agents") loadAgents();
    if (view === "skills") loadSkills();
    if (view === "mcp") loadMcpCatalog();
    if (view === "providers") loadProviders();
  }

  // ═══════════ CHAT ═══════════

  function startNewChat() {
    state.currentMessages = [];
    $("#messages").innerHTML = "";
    $("#chat-empty").style.display = "flex";
    $$(".session-item").forEach((el) => el.classList.remove("active"));
    switchView("chat");
  }

  function appendMessage(role, content, { streaming = false } = {}) {
    $("#chat-empty").style.display = "none";
    const wrap = document.createElement("div");
    wrap.className = `msg ${role}${streaming ? " streaming" : ""}`;
    const avatarLabel = role === "user" ? "You" : "G";
    wrap.innerHTML = `
      <div class="msg-avatar">${avatarLabel}</div>
      <div class="msg-body">
        <div class="msg-role">${role === "user" ? "You" : "GIA"}</div>
        <div class="msg-content"></div>
      </div>`;
    wrap.querySelector(".msg-content").innerHTML = renderContent(content);
    $("#messages").appendChild(wrap);
    scrollChatToBottom();
    return wrap;
  }

  function scrollChatToBottom() {
    const el = $("#chat-scroll");
    el.scrollTop = el.scrollHeight;
  }

  function autoGrowTextarea(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }

  async function sendMessage(text) {
    if (!text.trim() || state.streaming) return;
    appendMessage("user", text);
    state.currentMessages.push({ role: "user", content: text });

    const input = $("#composer-input");
    input.value = "";
    autoGrowTextarea(input);
    setSendEnabled();

    const assistantEl = appendMessage("assistant", "", { streaming: true });
    const contentEl = assistantEl.querySelector(".msg-content");
    state.streaming = true;
    setStatus("busy");

    try {
      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });

      if (!res.ok || !res.body) {
        throw new Error(`Server error (${res.status})`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let full = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const rawEvent = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const line = rawEvent.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          const payload = line.slice(5).trim();
          if (!payload) continue;
          let evt;
          try {
            evt = JSON.parse(payload);
          } catch {
            continue;
          }
          if (evt.type === "delta") {
            full += evt.content;
            contentEl.innerHTML = renderContent(full);
            scrollChatToBottom();
          } else if (evt.type === "error") {
            full += `\n\n⚠ ${evt.error}`;
            contentEl.innerHTML = renderContent(full);
          } else if (evt.type === "done") {
            if (!full && evt.result && evt.result.message) {
              full = evt.result.message;
              contentEl.innerHTML = renderContent(full);
            }
            if (evt.result && evt.result.duration_ms != null) {
              const meta = document.createElement("div");
              meta.className = "msg-meta";
              meta.textContent = `${evt.result.duration_ms}ms · ${evt.result.tool_calls || 0} tool call(s)`;
              assistantEl.querySelector(".msg-body").appendChild(meta);
            }
          }
        }
      }

      state.currentMessages.push({ role: "assistant", content: full });
    } catch (err) {
      contentEl.innerHTML = renderContent(`⚠ ${err.message || "Something went wrong reaching Nexus."}`);
    } finally {
      assistantEl.classList.remove("streaming");
      state.streaming = false;
      setStatus("online");
      setSendEnabled();
    }
  }

  function setSendEnabled() {
    const input = $("#composer-input");
    $("#composer-send").disabled = state.streaming || !input.value.trim();
  }

  function initComposer() {
    const input = $("#composer-input");
    input.addEventListener("input", () => {
      autoGrowTextarea(input);
      setSendEnabled();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage(input.value);
      }
    });
    $("#composer-send").addEventListener("click", () => sendMessage(input.value));

    $$(".suggestion-card").forEach((card) => {
      card.addEventListener("click", () => sendMessage(card.dataset.prompt));
    });

    setSendEnabled();
  }

  // ═══════════ STATUS / VITALS ═══════════

  function setStatus(state_) {
    const el = $("#connection-status");
    el.className = `status-pill ${state_}`;
    const labels = { online: "online", offline: "offline", busy: "thinking…", connecting: "connecting…" };
    el.querySelector ? null : null;
    el.innerHTML = `<span class="dot"></span>${labels[state_] || state_}`;
  }

  async function refreshStatus() {
    try {
      const res = await fetch("/api/status");
      if (!res.ok) throw new Error();
      const data = await res.json();
      setStatus("online");
      const activeProvider = data.providers && data.providers.active;
      $("#active-provider-label").textContent = activeProvider ? activeProvider : "No provider configured";
    } catch {
      setStatus("offline");
    }
  }

  async function refreshVitals() {
    try {
      const res = await fetch("/api/vitals");
      const data = await res.json();
      $("#vital-cpu").textContent = `CPU ${data.cpu ?? "—"}`;
      $("#vital-disk").textContent = `Disk ${data.disk ?? "—"}`;
    } catch {
      /* non-fatal */
    }
  }

  async function refreshSessions() {
    try {
      const res = await fetch("/api/sessions");
      const data = await res.json();
      const sessions = data.sessions || [];
      const list = $("#session-list");
      if (!sessions.length) {
        list.innerHTML = `<div class="session-empty">No conversations yet</div>`;
        return;
      }
      list.innerHTML = "";
      sessions.forEach((s) => {
        const item = document.createElement("div");
        item.className = "session-item";
        const firstUserMsg = (s.messages || []).find((m) => m.role === "user");
        const title = s.title || s.summary || (firstUserMsg && firstUserMsg.content) || s.id || "Untitled chat";
        item.textContent = title.length > 48 ? title.slice(0, 48) + "…" : title;
        list.appendChild(item);
      });
    } catch {
      /* non-fatal: sessions API may not be wired to a persistence backend yet */
    }
  }

  // ═══════════ AGENTS ═══════════

  async function loadAgents() {
    const grid = $("#agents-grid");
    grid.innerHTML = `<div class="muted">Loading agents…</div>`;
    try {
      const res = await fetch("/api/agent/list");
      const data = await res.json();
      const agents = data.agents || [];
      $("#nav-agents-count").textContent = agents.length;
      if (!agents.length) {
        grid.innerHTML = `<div class="muted">No active agents. Spawn one above.</div>`;
        return;
      }
      grid.innerHTML = "";
      agents.forEach((a) => {
        const card = document.createElement("div");
        card.className = "card";
        card.innerHTML = `
          <div class="card-head">
            <span class="card-title">${escapeHtml(a.name || a.id || "agent")}</span>
            <span class="card-badge ${a.status === "running" ? "on" : ""}">${escapeHtml(a.status || "idle")}</span>
          </div>
          <div class="card-desc">${escapeHtml(a.role || "")}</div>
          <div class="card-foot"><span class="card-tag">${escapeHtml(a.id || "")}</span></div>`;
        grid.appendChild(card);
      });
    } catch {
      grid.innerHTML = `<div class="muted">Could not reach the agent API.</div>`;
    }
  }

  function initAgentSpawn() {
    $("#agent-spawn-btn").addEventListener("click", async () => {
      const task = $("#agent-task-input").value.trim();
      const role = $("#agent-role-select").value;
      if (!task) return;
      const btn = $("#agent-spawn-btn");
      btn.disabled = true;
      try {
        await fetch("/api/agent/spawn", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ task, role }),
        });
        $("#agent-task-input").value = "";
        await loadAgents();
      } finally {
        btn.disabled = false;
      }
    });
  }

  // ═══════════ SKILLS ═══════════

  let allSkills = [];

  async function loadSkills() {
    const grid = $("#skills-grid");
    grid.innerHTML = `<div class="muted">Loading skills…</div>`;
    try {
      const res = await fetch("/api/skills");
      const data = await res.json();
      allSkills = data.skills || [];
      $("#nav-skills-count").textContent = allSkills.length;
      renderSkills(allSkills);
    } catch {
      grid.innerHTML = `<div class="muted">Could not reach the skills API.</div>`;
    }
  }

  function renderSkills(skills) {
    const grid = $("#skills-grid");
    if (!skills.length) {
      grid.innerHTML = `<div class="muted">No skills match your search.</div>`;
      return;
    }
    grid.innerHTML = "";
    skills.slice(0, 200).forEach((s) => {
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `
        <div class="card-head">
          <span class="card-title">${escapeHtml(s.name)}</span>
          <span class="card-badge ${s.active ? "on" : ""}">${s.active ? "active" : s.category}</span>
        </div>
        <div class="card-desc">${escapeHtml((s.description || "").slice(0, 140))}</div>
        <div class="card-foot"><span class="card-tag">${escapeHtml(s.category || "")}</span></div>`;
      grid.appendChild(card);
    });
  }

  function initSkillsSearch() {
    $("#skills-search").addEventListener("input", (e) => {
      const q = e.target.value.toLowerCase();
      renderSkills(
        allSkills.filter(
          (s) =>
            s.name.toLowerCase().includes(q) ||
            (s.description || "").toLowerCase().includes(q) ||
            (s.category || "").toLowerCase().includes(q)
        )
      );
    });
  }

  // ═══════════ MCP MARKETPLACE ═══════════

  async function loadMcpCatalog() {
    const grid = $("#mcp-grid");
    grid.innerHTML = `<div class="muted">Loading MCP marketplace…</div>`;
    try {
      const res = await fetch("/api/mcp/catalog");
      const data = await res.json();
      $("#nav-mcp-count").textContent = data.total ?? (data.servers || []).length;

      const catSelect = $("#mcp-category-select");
      if (catSelect.options.length <= 1) {
        (data.categories || []).forEach((c) => {
          const opt = document.createElement("option");
          opt.value = c;
          opt.textContent = c;
          catSelect.appendChild(opt);
        });
      }
      renderMcp(data.servers || []);
    } catch {
      grid.innerHTML = `<div class="muted">Could not reach the MCP marketplace API.</div>`;
    }
  }

  function renderMcp(servers) {
    const grid = $("#mcp-grid");
    if (!servers.length) {
      grid.innerHTML = `<div class="muted">No MCP servers match.</div>`;
      return;
    }
    grid.innerHTML = "";
    servers.forEach((s) => {
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `
        <div class="card-head">
          <span class="card-title">${escapeHtml(s.name)}</span>
          <span class="card-badge ${s.installed ? "on" : ""}">${s.installed ? "installed" : s.transport}</span>
        </div>
        <div class="card-desc">${escapeHtml(s.description || "No description available.")}</div>
        <div class="card-foot">
          <span class="card-tag">${escapeHtml(s.category)}</span>
          <button class="btn-ghost mcp-install-btn" data-name="${escapeHtml(s.name)}" ${s.installed ? "disabled" : ""}>
            ${s.installed ? "Installed" : "Install"}
          </button>
        </div>`;
      grid.appendChild(card);
    });

    $$(".mcp-install-btn", grid).forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "Installing…";
        try {
          const res = await fetch("/api/mcp/install", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: btn.dataset.name }),
          });
          const result = await res.json();
          if (result.status === "success") {
            btn.textContent = "Installed";
          } else {
            btn.textContent = "Failed";
            btn.disabled = false;
          }
        } catch {
          btn.textContent = "Failed";
          btn.disabled = false;
        }
      });
    });
  }

  function initMcpFilters() {
    let query = "";
    let category = "";
    const apply = async () => {
      const grid = $("#mcp-grid");
      grid.innerHTML = `<div class="muted">Searching…</div>`;
      const params = new URLSearchParams();
      if (query) params.set("q", query);
      if (category) params.set("category", category);
      const res = await fetch(`/api/mcp/catalog?${params.toString()}`);
      const data = await res.json();
      renderMcp(data.servers || []);
    };
    $("#mcp-search").addEventListener("input", (e) => {
      query = e.target.value;
      apply();
    });
    $("#mcp-category-select").addEventListener("change", (e) => {
      category = e.target.value;
      apply();
    });
  }

  // ═══════════ PROVIDERS ═══════════

  async function loadProviders() {
    const grid = $("#providers-grid");
    grid.innerHTML = `<div class="muted">Loading providers…</div>`;
    try {
      const res = await fetch("/api/providers");
      const data = await res.json();
      const providers = data.providers || data || [];
      if (!Array.isArray(providers) || !providers.length) {
        grid.innerHTML = `<div class="muted">No providers configured yet. Run <code>nexus setup</code> in your terminal.</div>`;
        return;
      }
      grid.innerHTML = "";
      providers.forEach((p) => {
        const card = document.createElement("div");
        card.className = "card";
        card.innerHTML = `
          <div class="card-head">
            <span class="card-title">${escapeHtml(p.name || "")}</span>
            <span class="card-badge ${p.active ? "on" : ""}">${p.active ? "active" : "configured"}</span>
          </div>
          <div class="card-desc">${escapeHtml(p.model || "")}</div>`;
        grid.appendChild(card);
      });
    } catch {
      grid.innerHTML = `<div class="muted">Could not reach the providers API.</div>`;
    }
  }

  // ═══════════ INIT ═══════════

  document.addEventListener("DOMContentLoaded", () => {
    initSidebar();
    initComposer();
    initAgentSpawn();
    initSkillsSearch();
    initMcpFilters();

    refreshStatus();
    refreshVitals();
    refreshSessions();

    setInterval(refreshStatus, 15000);
    setInterval(refreshVitals, 10000);
  });
})();
