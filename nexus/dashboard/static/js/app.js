const state = { provider: null, tools: [], skills: [], sessions: [], facts: [], agents: [] };

/* ── navigation ── */

document.querySelectorAll('.sidebar nav a').forEach(a => {
  a.addEventListener('click', e => {
    e.preventDefault();
    document.querySelectorAll('.sidebar nav a').forEach(x => x.classList.remove('active'));
    a.classList.add('active');
    const section = a.dataset.section;
    document.querySelectorAll('.content > section').forEach(s => s.style.display = 'none');
    const el = document.getElementById('section-' + section);
    if (el) el.style.display = 'block';
    document.getElementById('section-title').textContent = section.charAt(0).toUpperCase() + section.slice(1);
    if (section === 'projects') loadProjects();
    if (section === 'agents') loadAgents();
    if (section === 'settings') loadSettings();
  });
});

/* ── API helper ── */

async function api(path, opts = {}) {
  try {
    const res = await fetch(path, opts);
    return await res.json();
  } catch { return null; }
}

/* ── overview ── */

async function loadOverview() {
  const [status, tools, skills, facts, sessions, automation] = await Promise.all([
    api('/api/status'), api('/api/tools'), api('/api/skills'),
    api('/api/facts'), api('/api/sessions'), api('/api/automation/status'),
  ]);
  if (status) {
    state.provider = status.providers?.active || null;
    const ok = status.providers?.configured?.length > 0;
    document.getElementById('stat-status').innerHTML = ok
      ? '<span style="color:var(--green)">● Online</span>'
      : '<span style="color:var(--yellow)">● Limited</span>';
    document.getElementById('nav-status-dot').className = 'status-dot ' + (ok ? 'online' : 'offline');
    document.getElementById('stat-provider').textContent = status.providers?.active || 'None';
    document.getElementById('stat-provider-model').textContent = status.system?.python_version || '';
    document.getElementById('header-provider').textContent = status.providers?.active
      ? 'Provider: ' + status.providers.active : 'No provider configured';
  }
  if (tools && tools.tools) {
    state.tools = tools.tools;
    document.getElementById('stat-tools').textContent = tools.tools.length;
    document.getElementById('nav-tools-count').textContent = tools.tools.length;
    renderTools(tools.tools);
  }
  if (skills && skills.skills) {
    state.skills = skills.skills;
    const active = skills.skills.filter(s => s.active).length;
    document.getElementById('stat-skills').textContent = active + '/' + skills.skills.length;
    document.getElementById('nav-skills-count').textContent = skills.skills.length;
    renderSkills(skills.skills);
  }
  if (facts && facts.facts) {
    state.facts = facts.facts;
    document.getElementById('stat-facts').textContent = facts.facts.length;
    renderFacts(facts.facts);
  }
  if (sessions && sessions.sessions) {
    state.sessions = sessions.sessions;
    document.getElementById('stat-sessions').textContent = sessions.sessions.length;
    document.getElementById('nav-sessions-count').textContent = sessions.sessions.length;
    renderSessions(sessions.sessions);
  }
  if (automation) renderAutomation(automation);
}

/* ── providers ── */

async function loadProviders() {
  const data = await api('/api/providers');
  renderProviders(data);
  if (data && data.providers) {
    document.getElementById('nav-providers-count').textContent = data.providers.length;
  }
}

function renderProviders(data) {
  const el = document.getElementById('providers-list');
  if (!data || !data.providers || !data.providers.length) {
    el.innerHTML = '<div class="empty-state">No providers configured</div>';
    return;
  }
  el.innerHTML = data.providers.map(p => `
    <div class="provider-card">
      <div>
        <div class="name">${p.name}</div>
        <div class="model">${p.model || 'default'} &middot; ${p.type}</div>
      </div>
      <div class="flex items-center gap-2">
        <span class="tag ${p.active ? 'active' : ''}">${p.active ? 'Active' : 'Inactive'}</span>
        <div class="actions">
          ${!p.active ? `<button onclick="activateProvider('${p.name}')">Activate</button>` : ''}
          <button class="danger" onclick="removeProvider('${p.name}')">Remove</button>
        </div>
      </div>
    </div>
  `).join('');
}

async function activateProvider(name) {
  await api('/api/providers/' + encodeURIComponent(name) + '/activate', { method: 'POST' });
  loadProviders();
}

async function removeProvider(name) {
  await api('/api/providers', { method: 'DELETE', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name}) });
  loadProviders();
}

function showAddProvider() {
  document.getElementById('provider-form').style.display = 'block';
}

function hideAddProvider() {
  document.getElementById('provider-form').style.display = 'none';
}

async function addProvider() {
  const data = {
    name: document.getElementById('prov-name').value,
    provider_type: document.getElementById('prov-type').value,
    api_key: document.getElementById('prov-key').value,
    model: document.getElementById('prov-model').value,
    base_url: document.getElementById('prov-url').value,
  };
  await api('/api/providers', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
  hideAddProvider();
  loadProviders();
}

/* ── tools ── */

function renderTools(tools) {
  const el = document.getElementById('tools-list');
  if (!tools || !tools.length) { el.innerHTML = '<div class="empty-state">No tools registered</div>'; return; }
  el.innerHTML = tools.map(t => `
    <span class="tool-item"><span class="dot"></span> ${t.name} <span style="color:var(--text-dim);font-size:9px">${t.category || ''}</span></span>
  `).join('');
}

/* ── skills ── */

function renderSkills(skills) {
  const el = document.getElementById('skills-list');
  if (!skills || !skills.length) { el.innerHTML = '<div class="empty-state">No skills loaded</div>'; return; }
  el.innerHTML = skills.map(s => `
    <span class="skill-badge ${s.active ? 'active' : ''}" onclick="toggleSkill('${s.name}')">
      ${s.active ? '●' : '○'} ${s.name}
    </span>
  `).join('');
}

async function toggleSkill(name) {
  await api('/api/skills/' + encodeURIComponent(name) + '/activate', { method: 'POST' });
  const data = await api('/api/skills');
  if (data && data.skills) renderSkills(data.skills);
}

/* ── agents ── */

async function loadAgents() {
  const data = await api('/api/agent/list');
  if (data && data.agents) {
    state.agents = data.agents;
    document.getElementById('nav-agents-count').textContent = data.agents.length;
    const el = document.getElementById('agents-list');
    if (!data.agents.length) {
      el.innerHTML = '<div class="empty-state">No active agents. Spawn one above.</div>';
      return;
    }
    el.innerHTML = data.agents.map(a => `
      <div class="agent-card">
        <div class="info">
          <div class="name">${a.name}</div>
          <div class="meta"><span>${a.role}</span><span>ID: ${a.id.slice(0,12)}...</span></div>
        </div>
        <div class="flex items-center gap-2">
          <span class="status-badge ${a.status === 'error' ? 'error' : a.status === 'idle' ? 'idle' : ''}">${a.status}</span>
          <button class="btn btn-sm btn-danger" onclick="killAgent('${a.id}')">Kill</button>
        </div>
      </div>
    `).join('');
  }
}

async function spawnAgent() {
  const task = document.getElementById('spawn-task').value;
  const role = document.getElementById('spawn-role').value;
  if (!task) return;
  await api('/api/agent/spawn', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({task, role}) });
  document.getElementById('spawn-task').value = '';
  loadAgents();
}

async function killAgent(id) {
  await api('/api/agent/kill', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}) });
  loadAgents();
}

/* ── sessions ── */

function renderSessions(sessions) {
  const el = document.getElementById('sessions-list');
  if (!sessions || !sessions.length) { el.innerHTML = '<div class="empty-state">No sessions yet</div>'; return; }
  el.innerHTML = sessions.map(s => `
    <div class="session-item">
      <span class="id">${s.id ? s.id.slice(0,16) + '...' : '—'}</span>
      <span class="turns">${s.turn_count || 0} turns</span>
    </div>
  `).join('');
}

/* ── facts ── */

function renderFacts(facts) {
  const el = document.getElementById('facts-list');
  if (!facts || !facts.length) { el.innerHTML = '<div class="empty-state">No facts stored</div>'; return; }
  el.innerHTML = facts.map(f => `<div class="fact-item">${f.key || f.content || '—'}</div>`).join('');
}

/* ── automation ── */

function renderAutomation(data) {
  const el = document.getElementById('automation-status');
  const tools = data.automation_tools || [];
  el.innerHTML = `
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
      <span class="tool-item" style="background:${data.playwright_available ? 'rgba(0,230,118,.08)' : 'var(--bg)'};border-color:${data.playwright_available ? 'var(--green)' : 'var(--border)'}">● Playwright</span>
      <span class="tool-item" style="background:${data.browser_session_active ? 'rgba(0,230,118,.08)' : 'var(--bg)'};border-color:${data.browser_session_active ? 'var(--green)' : 'var(--border)'}">● Browser Session</span>
      <span class="tool-item" style="background:${data.httpx_available ? 'rgba(0,230,118,.08)' : 'var(--bg)'};border-color:${data.httpx_available ? 'var(--green)' : 'var(--border)'}">● HTTP Client</span>
    </div>
    <div style="font-size:11px;color:var(--text-dim)">${tools.length} automation tools configured</div>
  `;
}

/* ── projects ── */

async function loadProjects() {
  const root = document.getElementById('project-root').value || '.';
  const data = await api('/api/projects/tree?path=' + encodeURIComponent(root) + '&depth=4');
  const el = document.getElementById('project-tree');
  if (data.error) { el.innerHTML = '<div class="empty-state">' + data.error + '</div>'; return; }
  el.innerHTML = renderTree(data.tree || []);
}

function renderTree(nodes) {
  if (!nodes || !nodes.length) return '<div class="empty-state">Empty directory</div>';
  return '<div>' + nodes.map(n => {
    if (n.type === 'dir') {
      return `<div class="tree-node">
        <div class="tree-toggle" onclick="toggleDir(this)">
          <span class="icon folder">▶</span> ${n.name}/
        </div>
        <div class="tree-children" style="display:none">${renderTree(n.children)}</div>
      </div>`;
    }
    return `<div class="tree-file" onclick="openFile('${n.path}')">
      <span class="icon">●</span> ${n.name}
    </div>`;
  }).join('') + '</div>';
}

function toggleDir(el) {
  const children = el.nextElementSibling;
  const icon = el.querySelector('.icon');
  if (children.style.display === 'none') {
    children.style.display = 'block';
    icon.textContent = '▼';
  } else {
    children.style.display = 'none';
    icon.textContent = '▶';
  }
}

async function openFile(path) {
  const data = await api('/api/projects/read?path=' + encodeURIComponent(path));
  const el = document.getElementById('project-editor');
  if (data.error) { el.innerHTML = '<div class="empty-state">' + data.error + '</div>'; return; }
  el.innerHTML = `
    <div class="flex items-center justify-between mb-2">
      <span class="text-sm text-dim truncate" style="flex:1">${data.path}</span>
      <button class="btn btn-sm btn-primary" onclick="saveFile('${data.path}')">Save</button>
    </div>
    <textarea class="file-editor" id="file-editor-content">${escapeHtml(data.content)}</textarea>
  `;
}

async function saveFile(path) {
  const content = document.getElementById('file-editor-content').value;
  const data = await api('/api/projects/write', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path, content}),
  });
  const status = document.getElementById('project-editor').querySelector('.btn-primary');
  const orig = status.textContent;
  status.textContent = data.status === 'saved' ? '✔ Saved' : '✘ Error';
  setTimeout(() => status.textContent = orig, 2000);
}

/* ── settings ── */

async function loadSettings() {
  const data = await api('/api/settings');
  if (!data) return;
  if (data.user_name) document.getElementById('set-user').value = data.user_name;
  if (data.tool_profile) document.getElementById('set-profile').value = data.tool_profile;
  if (data.search_provider) document.getElementById('set-search').value = data.search_provider;
  if (data.log_level) document.getElementById('set-loglevel').value = data.log_level;
  document.getElementById('set-sandbox').checked = !!data.sandbox_mode;
  document.getElementById('set-termux').checked = !!data.termux_mode;
}

async function saveSettings() {
  const data = {
    user_name: document.getElementById('set-user').value,
    tool_profile: document.getElementById('set-profile').value,
    search_provider: document.getElementById('set-search').value,
    log_level: document.getElementById('set-loglevel').value,
    sandbox_mode: document.getElementById('set-sandbox').checked,
    termux_mode: document.getElementById('set-termux').checked,
  };
  const res = await api('/api/settings', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
  });
  const status = document.getElementById('settings-status');
  status.textContent = res.status === 'saved' ? '✔ Saved' : '✘ Error';
  setTimeout(() => status.textContent = '', 3000);
}

/* ── console / terminal ── */

const consoleBody = document.getElementById('console-body');
const cmdInput = document.getElementById('cmd-input');
let logLines = [];

function log(msg) {
  logLines.push(msg);
  if (logLines.length > 100) logLines.shift();
  consoleBody.innerHTML = logLines.map(l => '<div>' + l.replace(/</g, '&lt;') + '</div>').join('');
  consoleBody.scrollTop = consoleBody.scrollHeight;
}

cmdInput.addEventListener('keydown', async e => {
  if (e.key === 'Enter') {
    const val = cmdInput.value.trim();
    if (!val) return;
    log('❯ ' + val);
    cmdInput.value = '';
    const result = await api('/api/execute', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({task: val}),
    });
    log(result?.message || 'Done.');
  }
});

/* ── SSE real-time events ── */

function connectSSE() {
  const evtSource = new EventSource('/api/events');
  evtSource.onmessage = e => {
    try {
      const data = JSON.parse(e.data);
      if (data.vitals) {
        if (data.vitals.cpu) document.getElementById('stat-status').innerHTML = '<span style="color:var(--green)">● Online</span>';
      }
    } catch {}
  };
}

/* ── time ── */

function updateTime() {
  document.getElementById('header-time').textContent = new Date().toLocaleTimeString();
  document.getElementById('console-timestamp').textContent = new Date().toLocaleTimeString();
}

/* ── utils ── */

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

/* ── init ── */

async function init() {
  log('Nexus Neural OS — console online');
  log('Type a command or navigate the dashboard.');
  updateTime();
  await Promise.all([loadOverview(), loadProviders()]);
  connectSSE();
  setInterval(() => { updateTime(); loadOverview(); loadProviders(); }, 10000);
}
init();
