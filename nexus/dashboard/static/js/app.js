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

  // ── auth: X-Nexus-Token on every API call ──────────────────
  // The backend requires the token for mutating routes (POST/PUT/DELETE)
  // and, in strict mode, for all /api routes. The token is printed once
  // when `nexus dashboard` starts (stored at ~/.nexus/dashboard-token);
  // here it is kept in localStorage after a one-time prompt. A 401
  // re-opens the prompt — e.g. when the token was rotated on restart —
  // and the failed request is retried once with the new token.
  const TOKEN_STORAGE_KEY = "nexus_dashboard_token";
  const TOKEN_HEADER = "X-Nexus-Token";

  function getStoredToken() {
    return localStorage.getItem(TOKEN_STORAGE_KEY) || "";
  }

  function promptForToken() {
    return new Promise((resolve) => {
      // Minimal modal: overlay + one password input. No framework,
      // matching the rest of this file. Save stores the token;
      // Cancel resolves without one (the next call will 401 again).
      const existing = document.getElementById("token-modal");
      if (existing) existing.remove();
      const overlay = document.createElement("div");
      overlay.id = "token-modal";
      overlay.style.cssText =
        "position:fixed;inset:0;background:rgba(0,0,0,.65);display:flex;align-items:center;justify-content:center;z-index:9999;";
      overlay.innerHTML = `
        <div style="background:#16181f;color:#e6e6e6;padding:24px;border-radius:12px;width:min(92vw,420px);font:14px/1.5 system-ui,sans-serif;border:1px solid #2a2e3a;">
          <div style="font-weight:600;margin-bottom:6px;">Dashboard token required</div>
          <div style="opacity:.75;margin-bottom:12px;">Paste the token printed when <code>nexus dashboard</code> started (also stored in <code>~/.nexus/dashboard-token</code>).</div>
          <input id="token-input" type="password" autocomplete="off" placeholder="${TOKEN_HEADER}" style="width:100%;box-sizing:border-box;padding:8px 10px;border-radius:8px;border:1px solid #2a2e3a;background:#0f1117;color:#e6e6e6;" />
          <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px;">
            <button id="token-cancel" style="padding:6px 12px;background:transparent;color:#9aa0b0;border:1px solid #2a2e3a;border-radius:8px;cursor:pointer;">Cancel</button>
            <button id="token-save" style="padding:6px 12px;background:#4f7cff;color:#fff;border:none;border-radius:8px;cursor:pointer;">Save token</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);
      const input = overlay.querySelector("#token-input");
      input.focus();
      const close = (save) => {
        if (save && input.value.trim()) {
          localStorage.setItem(TOKEN_STORAGE_KEY, input.value.trim());
        }
        overlay.remove();
        resolve();
      };
      overlay.querySelector("#token-save").addEventListener("click", () => close(true));
      overlay.querySelector("#token-cancel").addEventListener("click", () => close(false));
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") close(true);
        if (e.key === "Escape") close(false);
      });
    });
  }

  async function apiFetch(url, opts = {}) {
    let res = await fetch(url, withToken(opts));
    if (res.status === 401) {
      await promptForToken();
      res = await fetch(url, withToken(opts)); // retry once with the saved token
    }
    return res;
  }

  function withToken(opts) {
    return Object.assign({}, opts, {
      headers: Object.assign({}, opts.headers || {}, { [TOKEN_HEADER]: getStoredToken() }),
    });
  }

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
      const res = await apiFetch("/api/chat/stream", {
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
      // A turn may have loaded skills and consumed context — refresh the
      // stats strip and the ctx chip right away instead of waiting for
      // the 60s timer.
      refreshStats();
      refreshContext();
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
      const res = await apiFetch("/api/status");
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
      const res = await apiFetch("/api/vitals");
      const data = await res.json();
      $("#vital-cpu").textContent = `CPU ${data.cpu ?? "—"}`;
      $("#vital-disk").textContent = `Disk ${data.disk ?? "—"}`;
    } catch {
      /* non-fatal */
    }
  }

  async function refreshSessions() {
    try {
      const res = await apiFetch("/api/sessions");
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

  // ═══════════ AGENT-INTELLIGENCE STATS / CONTEXT ═══════════

  function setStat(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  /** Stats strip: ⚡ skills · ⚙ provider · 🧠 facts · 💬 sessions.
   * Any section the backend reports as null (subsystem unavailable)
   * renders as a "—" placeholder instead of breaking the strip. */
  async function refreshStats() {
    try {
      const res = await apiFetch("/api/stats");
      if (!res.ok) return;
      const data = await res.json();

      const top = Array.isArray(data.top_skills) ? data.top_skills : [];
      const skillsEl = $("#stat-skills");
      if (top.length) {
        skillsEl.textContent = `⚡ ${top.slice(0, 2).map((s) => `${s.name} ×${s.count}`).join(", ")}`;
        skillsEl.title =
          `Top skills: ${top.map((s) => `${s.name} (${s.count})`).join(", ")}` +
          (data.total_skill_loads != null ? ` · ${data.total_skill_loads} total loads` : "");
      } else {
        skillsEl.textContent = "⚡ —";
        skillsEl.title = data.total_skill_loads === 0 ? "No skills loaded yet" : "";
      }

      const p = data.provider || {};
      const provEl = $("#stat-provider");
      provEl.textContent = p.name ? `⚙ ${p.name}` : "⚙ —";
      provEl.title = p.model ? `Model: ${p.model}` : "";

      const mem = data.memory || {};
      setStat("stat-facts", mem.facts != null ? `🧠 ${mem.facts} facts` : "🧠 —");
      setStat("stat-sessions", mem.sessions != null ? `💬 ${mem.sessions} sessions` : "💬 —");
    } catch {
      /* non-fatal: stats strip stays on its last known values */
    }
  }

  /** Context chip near the composer: thin bar + `ctx 42%`. Hidden until
   * the orchestrator exists (i.e. before the first chat turn). */
  async function refreshContext() {
    const chip = $("#ctx-chip");
    if (!chip) return;
    try {
      const res = await apiFetch("/api/context");
      if (!res.ok) {
        chip.hidden = true;
        return;
      }
      const data = await res.json();
      if (!data.available || data.ratio == null) {
        chip.hidden = true;
        return;
      }
      const pct = Math.round((data.ratio || 0) * 100);
      chip.hidden = false;
      chip.className = `ctx-chip${pct >= 85 ? " hot" : pct >= 60 ? " warn" : ""}`;
      $("#ctx-chip-label").textContent = `ctx ${pct}%`;
      $("#ctx-bar-fill").style.width = `${Math.min(100, pct)}%`;
      chip.title = data.tokens != null && data.window != null
        ? `~${data.tokens.toLocaleString()} / ${data.window.toLocaleString()} tokens · ${data.messages} messages`
        : "";
    } catch {
      chip.hidden = true;
    }
  }

  // ═══════════ AGENTS ═══════════

  async function loadAgents() {
    const grid = $("#agents-grid");
    grid.innerHTML = `<div class="muted">Loading agents…</div>`;
    try {
      const res = await apiFetch("/api/agent/list");
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
        await apiFetch("/api/agent/spawn", {
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
      const res = await apiFetch("/api/skills");
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
      const res = await apiFetch("/api/mcp/catalog");
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
          const res = await apiFetch("/api/mcp/install", {
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
      const res = await apiFetch(`/api/mcp/catalog?${params.toString()}`);
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
      const res = await apiFetch("/api/providers");
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
    refreshStats();
    refreshContext();

    setInterval(refreshStatus, 15000);
    setInterval(refreshVitals, 10000);
    setInterval(refreshStats, 60000);
    setInterval(refreshContext, 60000);
  });
})();
