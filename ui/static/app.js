const state = {
  personas: [],
  models: [],
  conversations: [],
  activePersona: null,
  conversationId: null, // null = unsaved new chat; set once the first message round-trips
  modelOverride: null,  // null = use persona's own base model
};

const DEVICE_TOKEN_KEY = "portableai_device_token";
let deviceToken = localStorage.getItem(DEVICE_TOKEN_KEY) || localStorage.getItem("portableai.device_token") || null;

const el = (id) => document.getElementById(id);

function setDeviceToken(token) {
  deviceToken = token || null;
  try {
    if (token) localStorage.setItem(DEVICE_TOKEN_KEY, token);
    else localStorage.removeItem(DEVICE_TOKEN_KEY);
  } catch {
    // private mode
  }
}

function guessDeviceName() {
  const ua = navigator.userAgent || "";
  if (/iPhone/i.test(ua)) return "iPhone browser";
  if (/iPad/i.test(ua)) return "iPad browser";
  if (/Android/i.test(ua)) return "Android browser";
  return "LAN browser";
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (deviceToken) headers.Authorization = `Bearer ${deviceToken}`;
  const resp = await fetch(path, { ...opts, headers });
  const data = await resp.json().catch(() => ({}));
  if (resp.status === 401 && path !== "/api/pairing/claim") {
    setDeviceToken(null);
    showPairingGate(data.error === "pairing required" ? "" : (data.error || ""));
    const err = new Error(data.error || "Pairing required");
    err.pairingRequired = true;
    throw err;
  }
  if (!resp.ok) throw new Error(data.error || `request failed: ${resp.status}`);
  return data;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ---------- Tiny, dependency-free markdown renderer ----------
// Deliberately not pulling in marked.js/DOMPurify from a CDN: this UI is
// meant to work fully offline (same reasoning as USBMind), so it only
// covers the subset of markdown model replies actually use in practice:
// headers, bold/italic, inline code, fenced code blocks, lists, links,
// blockquotes, and paragraphs.
function renderMarkdown(raw) {
  if (!raw) return "";

  // 1. Pull out fenced code blocks first so nothing inside them gets
  //    touched by the inline replacements below.
  const codeBlocks = [];
  let text = raw.replace(/```[a-zA-Z0-9]*\n?([\s\S]*?)```/g, (_, code) => {
    const idx = codeBlocks.length;
    codeBlocks.push(`<pre><code>${escapeHtml(code.replace(/\n$/, ""))}</code></pre>`);
    return `\u0000CODEBLOCK${idx}\u0000`;
  });

  // 2. Escape everything else so raw HTML in a model reply can't inject
  //    markup. Markdown punctuation (*, _, `, [, ], #, >) isn't HTML-special
  //    so it survives this step untouched.
  text = escapeHtml(text);

  // 3. Inline replacements, order matters (code before bold/italic so
  //    punctuation inside `code spans` isn't reinterpreted).
  text = text.replace(/`([^`\n]+)`/g, (_, code) => `<code>${code}</code>`);
  text = text.replace(/\*\*([^*]+)\*\*/g, (_, s) => `<strong>${s}</strong>`);
  text = text.replace(/__([^_]+)__/g, (_, s) => `<strong>${s}</strong>`);
  text = text.replace(/\*([^*\n]+)\*/g, (_, s) => `<em>${s}</em>`);
  text = text.replace(/(?<![a-zA-Z0-9])_([^_\n]+)_(?![a-zA-Z0-9])/g, (_, s) => `<em>${s}</em>`);
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) =>
    `<a href="${url}" target="_blank" rel="noopener">${label}</a>`
  );

  // 4. Block-level: headers, lists, blockquotes, paragraphs.
  const lines = text.split("\n");
  let html = "";
  let inUl = false;
  let inOl = false;
  let paragraphBuffer = [];

  const flushParagraph = () => {
    if (paragraphBuffer.length) {
      html += `<p>${paragraphBuffer.join("<br>")}</p>`;
      paragraphBuffer = [];
    }
  };
  const closeLists = () => {
    if (inUl) { html += "</ul>"; inUl = false; }
    if (inOl) { html += "</ol>"; inOl = false; }
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();

    if (line.startsWith("\u0000CODEBLOCK")) {
      flushParagraph();
      closeLists();
      html += line;
      continue;
    }
    if (!line) {
      flushParagraph();
      closeLists();
      continue;
    }

    const header = line.match(/^(#{1,3})\s+(.*)/);
    if (header) {
      flushParagraph();
      closeLists();
      const level = header[1].length;
      html += `<h${level}>${header[2]}</h${level}>`;
      continue;
    }

    const ul = line.match(/^[-*]\s+(.*)/);
    if (ul) {
      flushParagraph();
      if (inOl) { html += "</ol>"; inOl = false; }
      if (!inUl) { html += "<ul>"; inUl = true; }
      html += `<li>${ul[1]}</li>`;
      continue;
    }

    const ol = line.match(/^\d+\.\s+(.*)/);
    if (ol) {
      flushParagraph();
      if (inUl) { html += "</ul>"; inUl = false; }
      if (!inOl) { html += "<ol>"; inOl = true; }
      html += `<li>${ol[1]}</li>`;
      continue;
    }

    const bq = line.match(/^&gt;\s?(.*)/);
    if (bq) {
      flushParagraph();
      closeLists();
      html += `<blockquote>${bq[1]}</blockquote>`;
      continue;
    }

    closeLists();
    paragraphBuffer.push(line);
  }
  flushParagraph();
  closeLists();

  // 5. Reinsert code blocks (already escaped when extracted in step 1).
  html = html.replace(/\u0000CODEBLOCK(\d+)\u0000/g, (_, idx) => codeBlocks[Number(idx)]);
  return html;
}

// ---------- Status ----------

async function refreshStatus() {
  const pill = el("statusPill");
  const text = el("statusText");
  try {
    const data = await api("/api/status");
    if (data.ollama_available) {
      pill.className = "status-pill status-ok";
      text.textContent = `Ollama connected (${data.base_url})`;
    } else {
      pill.className = "status-pill status-down";
      text.textContent = "Ollama not reachable";
    }
  } catch (err) {
    pill.className = "status-pill status-down";
    text.textContent = err.pairingRequired ? "Pairing required" : "Status check failed";
  }
}

// ---------- Models ----------

async function loadModels() {
  try {
    const data = await api("/api/models");
    state.models = data.models || [];
    el("modelsPathHint").textContent = data.models_path_hint || "unknown";
    renderModelsTable(state.models);
    populateModelSelect();
  } catch {
    // Ollama not reachable yet -- leave selector at "Persona default".
  }
}

async function loadCatalog() {
  try {
    const catalog = await api("/api/models/catalog");
    // Recommended entries first -- that's the point of marking them.
    catalog.sort((a, b) => (b.recommended ? 1 : 0) - (a.recommended ? 1 : 0));
    renderCatalogTable(catalog);
  } catch {
    el("catalogTable").innerHTML = '<p class="muted small">Could not load the model catalog.</p>';
  }
}

function renderCatalogTable(catalog) {
  const container = el("catalogTable");
  if (!catalog.length) {
    container.innerHTML = '<p class="muted small">No catalog entries found.</p>';
    return;
  }
  container.innerHTML = "";
  catalog.forEach((entry) => {
    const row = document.createElement("div");
    row.className = "model-row";
    row.dataset.search = [entry.name, entry.family, entry.parameter_size, entry.approx_size, entry.recommended ? "recommended" : ""]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    const info = document.createElement("span");
    const badge = entry.recommended
      ? `<span class="recommended-badge" title="${escapeHtml(entry.recommended_reason || "")}">★ Recommended</span>`
      : "";
    info.innerHTML = `<span class="model-name">${entry.name}</span>${badge}<br><span class="model-detail">${entry.family} · ${entry.parameter_size} · ~${entry.approx_size}</span>`;

    let action;
    if (entry.installed) {
      action = document.createElement("span");
      action.className = "installed-badge";
      action.textContent = "✓ Installed";
    } else {
      action = document.createElement("button");
      action.className = "download-btn";
      action.textContent = "Download";
      action.addEventListener("click", () => downloadModel(entry.name, action));
    }

    row.appendChild(info);
    row.appendChild(action);
    container.appendChild(row);
  });
  filterCatalogTable();
}

function filterCatalogTable() {
  const input = el("catalogSearch");
  const q = ((input && input.value) || "").trim().toLowerCase();
  el("catalogTable").querySelectorAll(".model-row").forEach((row) => {
    const hay = row.dataset.search || row.textContent.toLowerCase();
    row.hidden = Boolean(q) && !hay.includes(q);
  });
}

async function downloadModel(name, buttonEl) {
  buttonEl.disabled = true;
  buttonEl.textContent = "Downloading…";
  try {
    await api("/api/models/pull", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    await loadModels();
    await loadCatalog(); // re-render so this row now shows "Installed"
  } catch (err) {
    buttonEl.disabled = false;
    buttonEl.textContent = "Download";
    alert(`Download failed: ${err.message}`);
  }
}

function renderModelsTable(models) {
  const container = el("modelsTable");
  if (!models.length) {
    container.innerHTML = '<p class="muted small">No models found. Run `ollama pull &lt;model&gt;`.</p>';
    return;
  }
  container.innerHTML = "";
  models.forEach((m) => {
    const row = document.createElement("div");
    row.className = "model-row";
    row.innerHTML = `
      <span>
        <span class="model-name">${m.name}</span><br>
        <span class="model-detail">${[m.parameter_size, m.quantization, m.size_human].filter(Boolean).join(" · ")}</span>
      </span>
      <span>
        <button class="icon-btn model-update-btn" data-name="${m.name}" title="Check for updates">⟳</button>
      </span>
    `;
    container.appendChild(row);
  });
}

el("modelsTable").addEventListener("click", (e) => {
  const btn = e.target.closest(".model-update-btn");
  if (btn) checkModelUpdate(btn.dataset.name, btn);
});

async function checkModelUpdate(name, btn) {
  btn.disabled = true;
  btn.textContent = "…";
  try {
    const data = await api("/api/models/check-update", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    btn.textContent = data.updated ? "✓" : "=";
    btn.title = data.updated ? `Updated to a newer version` : "Already up to date";
    if (data.updated) await loadModels();
    setTimeout(() => {
      btn.textContent = "⟳";
      btn.title = "Check for updates";
      btn.disabled = false;
    }, 2500);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "⟳";
    alert(`Update check failed: ${err.message}`);
  }
}

function slugifyModelName(name) {
  return (name || "").replace(/:latest$/, "").replace(/[^a-zA-Z0-9_.-]/g, "-");
}

function isPersonaBuiltModel(name) {
  const base = (name || "").replace(/:latest$/, "");
  return state.personas.some((p) => p.id && (base === p.id || base.startsWith(`${p.id}--`)));
}

function currentPersona() {
  return state.personas.find((p) => p.id === state.activePersona) || null;
}

function personaLabel(p) {
  if (!p) return "";
  return p.display_name || p.id;
}

function personaLabelById(id) {
  const p = state.personas.find((x) => x.id === id);
  return personaLabel(p) || id || "";
}

function pickDefaultPersona(personas) {
  const list = personas || [];
  return list.find((p) => p.is_default && p.id) || list.find((p) => p.id) || null;
}

function markPersonaActive(id) {
  document.querySelectorAll(".persona-item").forEach((b) => {
    b.classList.toggle("active", b.dataset.personaId === id);
  });
}

function modelOverrideFromConversation(conv, persona) {
  if (!conv || !persona) return null;
  const used = (conv.model_used || "").replace(/:latest$/, "");
  if (!used || used === persona.base_model || used === persona.id) return null;
  const prefix = `${persona.id}--`;
  if (used.startsWith(prefix)) {
    const slug = used.slice(prefix.length);
    const match = state.models.find((m) => slugifyModelName(m.name) === slug);
    return match ? match.name.replace(/:latest$/, "") : null;
  }
  return used;
}

function populateModelSelect() {
  const select = el("modelSelect");
  const persona = currentPersona();
  const previous = state.modelOverride || "";
  select.innerHTML = "";
  const defaultOpt = document.createElement("option");
  defaultOpt.value = "";
  defaultOpt.textContent = persona ? `Persona default (${persona.base_model})` : "Persona default";
  select.appendChild(defaultOpt);
  state.models.forEach((m) => {
    if (isPersonaBuiltModel(m.name)) return;
    const opt = document.createElement("option");
    opt.value = m.name;
    opt.textContent = m.name;
    select.appendChild(opt);
  });
  if (previous && ![...select.options].some((o) => o.value === previous)) {
    const opt = document.createElement("option");
    opt.value = previous;
    opt.textContent = previous;
    select.appendChild(opt);
  }
  select.value = previous;
}

el("modelSelect").addEventListener("change", (e) => {
  state.modelOverride = e.target.value || null;
  // Switching the base model mid-conversation would mix context from two
  // different models -- start a fresh conversation instead.
  state.conversationId = null;
  updateChatHeader();
  showEmptyState(currentPersona());
  loadConversations();
});

const THEME_KEY = "portableai.theme";
const THEMES = ["dark", "light", "ube"];

function normalizeTheme(value) {
  return THEMES.includes(value) ? value : "dark";
}

function readStoredTheme() {
  try {
    return normalizeTheme(localStorage.getItem(THEME_KEY));
  } catch {
    return "dark";
  }
}

function applyTheme(theme) {
  const next = normalizeTheme(theme);
  document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {
    // private mode
  }
  const select = el("themeSelect");
  if (select && select.value !== next) select.value = next;
}

applyTheme(readStoredTheme());
el("themeSelect").addEventListener("change", (e) => applyTheme(e.target.value));

const SIDEBAR_COLLAPSED_KEY = "portableai.sidebar-collapsed";

function isMobileLayout() {
  return window.matchMedia("(max-width: 859px)").matches;
}

function closeMobileSidebar() {
  document.body.classList.remove("sidebar-open");
  const menu = el("menuBtn");
  if (menu) {
    menu.setAttribute("aria-expanded", "false");
    menu.setAttribute("aria-label", "Open chats");
  }
}

function openMobileSidebar() {
  document.body.classList.add("sidebar-open");
  const menu = el("menuBtn");
  if (menu) {
    menu.setAttribute("aria-expanded", "true");
    menu.setAttribute("aria-label", "Close chats");
  }
}

function toggleMobileSidebar() {
  if (document.body.classList.contains("sidebar-open")) closeMobileSidebar();
  else openMobileSidebar();
}

function setSidebarCollapsed(collapsed) {
  document.documentElement.classList.toggle("sidebar-collapsed", collapsed);
  try {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch {
    // private mode
  }
  const label = collapsed ? "Show sidebar" : "Hide sidebar";
  ["sidebarToggle", "sidebarOpenBtn"].forEach((id) => {
    const btn = el(id);
    if (!btn) return;
    btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    btn.title = label;
    btn.setAttribute("aria-label", label);
  });
}

function toggleDesktopSidebar() {
  setSidebarCollapsed(!document.documentElement.classList.contains("sidebar-collapsed"));
}

el("menuBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  toggleMobileSidebar();
});
el("sidebarBackdrop").addEventListener("click", closeMobileSidebar);
el("sidebarToggle").addEventListener("click", toggleDesktopSidebar);
el("sidebarOpenBtn").addEventListener("click", toggleDesktopSidebar);
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "b") return;
  e.preventDefault();
  if (isMobileLayout()) toggleMobileSidebar();
  else toggleDesktopSidebar();
});
window.addEventListener("resize", () => {
  if (!isMobileLayout()) closeMobileSidebar();
});
setSidebarCollapsed(document.documentElement.classList.contains("sidebar-collapsed"));

// ---------- Personas ----------

async function loadPersonas() {
  try {
    state.personas = await api("/api/personas");
  } catch {
    // pairing/network -- leave the list empty rather than an uncaught rejection
    return;
  }
  const list = el("personaList");
  list.innerHTML = "";
  state.personas.forEach((p) => {
    const btn = document.createElement("button");
    btn.className = "persona-item" + (p.id === state.activePersona ? " active" : "");
    btn.dataset.personaId = p.id;
    btn.type = "button";
    btn.innerHTML = `<span class="persona-name">${escapeHtml(personaLabel(p))}</span><span class="persona-model">${escapeHtml(p.base_model || "parse error")}</span>`;
    btn.onclick = () => selectPersona(p.id);
    list.appendChild(btn);
  });
  if (!state.activePersona) {
    const landing = pickDefaultPersona(state.personas);
    if (landing) selectPersona(landing.id);
  }
  populateModelSelect();
}

function selectPersona(id) {
  closeMobileSidebar();
  state.activePersona = id;
  state.conversationId = null;
  markPersonaActive(id);
  updateChatHeader();
  populateModelSelect();
  showEmptyState(currentPersona());
  loadConversations(); // clears "active" highlight on any previously-open chat
}

function updateChatHeader() {
  const persona = currentPersona();
  el("chatPersonaName").textContent = persona ? personaLabel(persona) : "Assistant";
  el("chatModelBadge").textContent = state.modelOverride || (persona ? persona.base_model : "—");
}

function emptyStateNode(persona) {
  const div = document.createElement("div");
  div.className = "empty-state";
  const h1 = document.createElement("h1");
  h1.id = "personaHeading";
  h1.textContent = persona ? personaLabel(persona) : "Say hello";
  const p = document.createElement("p");
  p.id = "personaSubheading";
  p.className = "muted";
  p.textContent = persona ? persona.system_preview : "";
  div.appendChild(h1);
  div.appendChild(p);
  return div;
}

function showEmptyState(persona) {
  const messages = el("messages");
  messages.innerHTML = "";
  messages.appendChild(emptyStateNode(persona));
}

function beginNewChat() {
  closeMobileSidebar();
  state.conversationId = null;
  const persona = currentPersona() || pickDefaultPersona(state.personas);
  if (persona && state.activePersona !== persona.id) {
    state.activePersona = persona.id;
    markPersonaActive(persona.id);
  }
  updateChatHeader();
  populateModelSelect();
  showEmptyState(persona);
  loadConversations();
}

// ---------- Chat ----------

function appendMessage(role, content, meta = "") {
  const messages = el("messages");
  const emptyState = messages.querySelector(".empty-state");
  if (emptyState) emptyState.remove();

  const row = document.createElement("div");
  row.className = `msg-row ${role === "error" ? "assistant" : role}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble" + (role === "error" ? " error" : "");

  const contentDiv = document.createElement("div");
  contentDiv.className = "bubble-content";
  if (role === "assistant") {
    contentDiv.innerHTML = renderMarkdown(content);
  } else {
    contentDiv.textContent = content;
  }
  bubble.appendChild(contentDiv);

  if (meta) {
    const metaEl = document.createElement("span");
    metaEl.className = "meta";
    metaEl.textContent = meta;
    bubble.appendChild(metaEl);
  }

  row.appendChild(bubble);
  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;
  return row;
}

function appendTyping() {
  const row = appendMessage("assistant", "");
  row.querySelector(".bubble").innerHTML = '<span class="typing-dots"><span></span><span></span><span></span></span>';
  return row;
}

async function sendMessage(text) {
  if (!state.activePersona) {
    appendMessage("error", "Pick a persona first.");
    return;
  }
  appendMessage("user", text);
  const typingRow = appendTyping();
  el("sendBtn").disabled = true;

  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        persona: state.activePersona,
        message: text,
        model_override: state.modelOverride,
        conversation_id: state.conversationId,
      }),
    });
    typingRow.remove();
    appendMessage("assistant", data.reply, `${data.model_used} · ${data.latency_ms} ms`);
    state.conversationId = data.conversation_id;
    loadConversations();
  } catch (err) {
    typingRow.remove();
    if (!err.pairingRequired) appendMessage("error", err.message);
  } finally {
    el("sendBtn").disabled = false;
  }
}

el("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = el("input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  input.style.height = "auto";
  sendMessage(text);
});

el("input").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
});

el("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    el("composer").requestSubmit();
  }
});

el("newChatBtn").addEventListener("click", beginNewChat);

// ---------- Conversation history (search / archive / rename / delete) ----------

function timeAgo(unixSeconds) {
  const diff = Date.now() / 1000 - unixSeconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function debounce(fn, wait) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

async function loadConversations() {
  const params = new URLSearchParams();
  if (el("archivedToggle").checked) params.set("archived", "1");
  const q = el("searchInput").value.trim();
  if (q) params.set("q", q);
  try {
    const convs = await api(`/api/conversations?${params.toString()}`);
    state.conversations = convs;
    renderConversationList(convs);
  } catch {
    // non-fatal -- sidebar just stays empty
  }
}

function renderConversationList(convs) {
  const container = el("conversationList");
  container.innerHTML = "";
  if (!convs.length) {
    container.innerHTML = '<p class="muted small" style="padding:8px 10px;">No chats yet.</p>';
    return;
  }
  convs.forEach((c) => {
    const row = document.createElement("div");
    row.className = "conversation-item" + (c.id === state.conversationId ? " active" : "");

    const titleBtn = document.createElement("button");
    titleBtn.className = "conversation-title";
    titleBtn.dataset.id = c.id;
    titleBtn.innerHTML = `<span>${escapeHtml(c.title || "New chat")}</span><span class="conversation-meta">${escapeHtml(personaLabelById(c.persona))} · ${timeAgo(c.updated_at)}</span>`;

    const actions = document.createElement("div");
    actions.className = "conversation-actions";
    actions.innerHTML = `
      <button class="icon-btn" data-action="export" data-id="${c.id}" title="Export as JSON">⬇</button>
      <button class="icon-btn" data-action="rename" data-id="${c.id}" title="Rename">✎</button>
      <button class="icon-btn" data-action="archive" data-id="${c.id}" data-archived="${c.archived ? "1" : "0"}" title="${c.archived ? "Unarchive" : "Archive"}">${c.archived ? "↩" : "🗄"}</button>
      <button class="icon-btn" data-action="delete" data-id="${c.id}" title="Delete">🗑</button>
    `;

    row.appendChild(titleBtn);
    row.appendChild(actions);
    container.appendChild(row);
  });
}

async function openConversation(id) {
  closeMobileSidebar();
  let conv;
  try {
    conv = await api(`/api/conversations/${id}`);
  } catch (err) {
    if (!err.pairingRequired) appendMessage("error", err.message);
    return;
  }
  state.conversationId = id;
  state.activePersona = conv.persona;
  const persona = state.personas.find((p) => p.id === conv.persona);
  state.modelOverride = modelOverrideFromConversation(conv, persona);

  markPersonaActive(conv.persona);
  updateChatHeader();
  populateModelSelect();

  el("messages").innerHTML = "";
  if (!conv.messages.length) {
    el("messages").appendChild(emptyStateNode(persona));
  } else {
    conv.messages.forEach((m) => {
      appendMessage(m.role, m.content, m.latency_ms ? `${m.latency_ms} ms` : "");
    });
  }
  loadConversations();
}

el("conversationList").addEventListener("click", async (e) => {
  const actionBtn = e.target.closest(".icon-btn");
  if (actionBtn) {
    e.stopPropagation();
    const id = actionBtn.dataset.id;
    const action = actionBtn.dataset.action;

    if (action === "export") {
      try {
        const conv = await api(`/api/conversations/${id}/export`);
        const blob = new Blob([JSON.stringify(conv, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${(conv.title || "chat").replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.json`;
        a.click();
        URL.revokeObjectURL(url);
      } catch (err) {
        alert(`Export failed: ${err.message}`);
      }
    } else if (action === "rename") {
      const title = window.prompt("Rename this chat:");
      if (title && title.trim()) {
        await api(`/api/conversations/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ title: title.trim() }),
        });
        loadConversations();
      }
    } else if (action === "archive") {
      const currentlyArchived = actionBtn.dataset.archived === "1";
      await api(`/api/conversations/${id}/archive`, {
        method: "POST",
        body: JSON.stringify({ archived: !currentlyArchived }),
      });
      loadConversations();
    } else if (action === "delete") {
      if (window.confirm("Delete this chat? This can't be undone.")) {
        await api(`/api/conversations/${id}`, { method: "DELETE" });
        if (state.conversationId === id) {
          state.conversationId = null;
          showEmptyState(currentPersona());
        }
        loadConversations();
      }
    }
    return;
  }

  const titleBtn = e.target.closest(".conversation-title");
  if (titleBtn) openConversation(titleBtn.dataset.id);
});

el("searchInput").addEventListener("input", debounce(() => loadConversations(), 250));
el("archivedToggle").addEventListener("change", () => loadConversations());

// ---------- Settings modal ----------

let pairingCountdownInterval = null;
let pairingPollInterval = null;

const SETTINGS_PANE_TITLES = {
  general: "General",
  installed: "Installed models",
  download: "Download",
  updates: "Updates",
  pairing: "Phone pairing",
};

function showSettingsPane(pane) {
  if (!pane) return;
  document.querySelectorAll(".settings-nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.pane === pane);
  });
  document.querySelectorAll(".settings-pane").forEach((section) => {
    section.classList.toggle("hidden", section.id !== `pane-${pane}`);
  });
  el("settingsPaneTitle").textContent = SETTINGS_PANE_TITLES[pane] || pane;
  el("settingsFooter").classList.toggle("hidden", pane !== "general" && pane !== "updates");
  if (pane === "pairing") loadPairingInfo();
}

function filterSettingsNav() {
  const q = (el("settingsSearch").value || "").trim().toLowerCase();
  document.querySelectorAll(".settings-nav-item").forEach((btn) => {
    const label = `${btn.dataset.search || ""} ${btn.textContent}`.toLowerCase();
    const pane = document.getElementById(`pane-${btn.dataset.pane}`);
    const paneHit = Boolean(q) && pane && pane.textContent.toLowerCase().includes(q);
    btn.hidden = Boolean(q) && !label.includes(q) && !paneHit;
  });
  document.querySelectorAll(".settings-nav-group").forEach((group) => {
    const any = [...group.querySelectorAll(".settings-nav-item")].some((b) => !b.hidden);
    group.hidden = Boolean(q) && !any;
  });
  const active = document.querySelector(".settings-nav-item.active");
  if (active && active.hidden) {
    const first = document.querySelector(".settings-nav-item:not([hidden])");
    if (first) showSettingsPane(first.dataset.pane);
  }
}

function openModal(id) { el(id).classList.remove("hidden"); }
function closeModal(id) {
  el(id).classList.add("hidden");
  if (id === "settingsModal") {
    if (pairingPollInterval) { clearInterval(pairingPollInterval); pairingPollInterval = null; }
    if (pairingCountdownInterval) { clearInterval(pairingCountdownInterval); pairingCountdownInterval = null; }
  }
}

document.querySelectorAll("[data-close]").forEach((btn) => {
  btn.addEventListener("click", () => closeModal(btn.dataset.close));
});

document.querySelector(".settings-nav").addEventListener("click", (e) => {
  const btn = e.target.closest(".settings-nav-item");
  if (!btn) return;
  showSettingsPane(btn.dataset.pane);
});

el("settingsSearch").addEventListener("input", filterSettingsNav);
const catalogSearch = el("catalogSearch");
if (catalogSearch) catalogSearch.addEventListener("input", filterCatalogTable);

el("settingsBtn").addEventListener("click", async () => {
  closeMobileSidebar();
  let settings;
  try {
    settings = await api("/api/settings");
  } catch (err) {
    if (err.pairingRequired) return;
    alert(`Could not open settings: ${err.message}`);
    return;
  }
  el("themeSelect").value = readStoredTheme();
  el("baseUrlInput").value = settings.base_url;
  el("autoBuildInput").checked = !!settings.auto_build_on_startup;
  el("updateUrlInput").value = settings.update_check_url || "";
  el("autoCheckUpdatesInput").checked = !!settings.auto_check_updates;
  el("updateStatusText").textContent = "Compares your hosted JSON against this app’s baked-in versions.";
  el("settingsSearch").value = "";
  filterSettingsNav();
  showSettingsPane("general");
  await loadModels();
  await loadCatalog();
  await loadPairingInfo();
  if (pairingPollInterval) { clearInterval(pairingPollInterval); pairingPollInterval = null; }
  pairingPollInterval = setInterval(loadPairingInfo, 5000);
  openModal("settingsModal");
});

el("saveSettingsBtn").addEventListener("click", async () => {
  await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      base_url: el("baseUrlInput").value.trim(),
      auto_build_on_startup: el("autoBuildInput").checked,
      update_check_url: el("updateUrlInput").value.trim(),
      auto_check_updates: el("autoCheckUpdatesInput").checked,
    }),
  });
  closeModal("settingsModal");
  refreshStatus();
  loadModels();
});

// ---------- Update checking (opt-in, off by default) ----------

function setUpdateDot(show) {
  el("settingsUpdateDot").classList.toggle("hidden", !show);
  const navDot = el("settingsNavUpdateDot");
  if (navDot) navDot.classList.toggle("hidden", !show);
}

async function runUpdateCheck({ silent = false } = {}) {
  if (!silent) el("updateStatusText").textContent = "Checking…";
  try {
    const data = await api("/api/updates/check");
    if (!data.enabled) {
      if (!silent) el("updateStatusText").textContent = "Not configured — add an update check URL above.";
      return;
    }
    if (!data.reachable) {
      if (!silent) el("updateStatusText").textContent = "Could not reach the update URL.";
      return;
    }
    const messages = [];
    if (data.app_update_available) messages.push(`App update available: ${data.remote_app_version}`);
    if (data.catalog_update_available) messages.push(`Model catalog update available: ${data.remote_catalog_version}`);
    const hasUpdate = data.app_update_available || data.catalog_update_available;
    if (!silent) {
      el("updateStatusText").textContent = hasUpdate
        ? messages.join(" · ") + (data.notes_url ? ` — ${data.notes_url}` : "")
        : "You're up to date.";
    }
    setUpdateDot(hasUpdate);
  } catch (err) {
    if (!silent) el("updateStatusText").textContent = err.message;
  }
}

el("checkUpdatesBtn").addEventListener("click", () => runUpdateCheck());

async function maybeAutoCheckUpdatesOnStartup() {
  try {
    const settings = await api("/api/settings");
    if (settings.auto_check_updates) {
      await runUpdateCheck({ silent: true });
    }
  } catch {
    // background/optional -- never surface an error for this
  }
}

// ---------- Phone pairing ----------

function startPairingCountdown(expiresAt) {
  if (pairingCountdownInterval) clearInterval(pairingCountdownInterval);
  pairingCountdownInterval = null;
  const countdownEl = el("pairingPinCountdown");
  if (!countdownEl) return;
  const chip = countdownEl.closest(".pairing-countdown");
  const labelEl = chip ? chip.querySelector(".pairing-countdown-label") : null;

  const render = (remaining) => {
    if (remaining == null) {
      countdownEl.textContent = "";
      if (labelEl) labelEl.textContent = "Expires in";
      if (chip) {
        chip.hidden = true;
        chip.classList.remove("is-expired");
      }
      return;
    }
    if (chip) chip.hidden = false;
    if (remaining <= 0) {
      if (chip) chip.classList.add("is-expired");
      if (labelEl) labelEl.textContent = "Expired";
      countdownEl.textContent = "refreshing...";
      return;
    }
    if (chip) chip.classList.remove("is-expired");
    if (labelEl) labelEl.textContent = "Expires in";
    const mins = Math.floor(remaining / 60);
    const secs = remaining % 60;
    countdownEl.textContent = `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const expires = Number(expiresAt);
  if (!Number.isFinite(expires) || expires <= 0) {
    render(null);
    return;
  }
  const remainingNow = Math.max(0, Math.round(expires - Date.now() / 1000));
  if (remainingNow <= 0) {
    render(0);
    return;
  }
  const tick = () => {
    const remaining = Math.max(0, Math.round(expires - Date.now() / 1000));
    render(remaining);
    if (remaining <= 0) {
      clearInterval(pairingCountdownInterval);
      pairingCountdownInterval = null;
      loadPairingInfo();
    }
  };
  tick();
  pairingCountdownInterval = setInterval(tick, 1000);
}

async function loadPairingInfo() {
  try {
    const data = await api("/api/pairing/pin");
    el("pairingPinText").textContent = data.pin;
    el("lanUrlText").textContent = data.lan_url;
    el("lanIpWarning").classList.toggle("hidden", data.lan_ip_detected !== false);
    // Cache-bust so the browser doesn't reuse a QR image for the old PIN.
    el("pairingQrImg").src = `/api/pairing/qr.svg?t=${Date.now()}`;
    startPairingCountdown(data.expires_at);
  } catch (err) {
    el("pairingPinText").textContent = "error";
    startPairingCountdown(null);
  }
  try {
    const devices = await api("/api/pairing/devices");
    renderPairedDevices(devices);
  } catch {
    el("pairedDevicesList").innerHTML = '<p class="muted small">Could not load paired devices.</p>';
  }
}

function renderPairedDevices(devices) {
  const container = el("pairedDevicesList");
  if (!devices.length) {
    container.innerHTML = '<p class="muted small">No devices paired yet.</p>';
    return;
  }
  container.innerHTML = "";
  devices.forEach((d) => {
    const row = document.createElement("div");
    row.className = "model-row";
    const paired = new Date(d.paired_at * 1000).toLocaleDateString();
    row.innerHTML = `
      <span>
        <span class="model-name">${escapeHtml(d.name)}</span><br>
        <span class="model-detail">paired ${paired}</span>
      </span>
      <button class="icon-btn" data-token="${d.token}" title="Revoke">🗑</button>
    `;
    container.appendChild(row);
  });
}

el("pairedDevicesList").addEventListener("click", async (e) => {
  const btn = e.target.closest(".icon-btn[data-token]");
  if (!btn) return;
  if (!window.confirm("Revoke this device? It will need to pair again with a new PIN.")) return;
  await api(`/api/pairing/devices/${btn.dataset.token}`, { method: "DELETE" });
  loadPairingInfo();
});

el("regeneratePinBtn").addEventListener("click", async () => {
  await api("/api/pairing/pin/regenerate", { method: "POST" });
  loadPairingInfo();
});

function showPairingGate(errorMessage) {
  const gate = el("pairingGate");
  if (!gate) return;
  gate.classList.remove("hidden");
  el("pairingGateError").textContent = errorMessage || "";
  const nameInput = el("pairingGateName");
  if (nameInput && !nameInput.value) nameInput.value = guessDeviceName();
  setTimeout(() => el("pairingGatePin").focus(), 50);
}

function hidePairingGate() {
  el("pairingGate").classList.add("hidden");
}

async function attemptPairing(pin, deviceName) {
  const resp = await fetch("/api/pairing/claim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin, device_name: deviceName || guessDeviceName() }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || "Pairing failed");
  if (!data.device_token) throw new Error("pairing did not return a token");
  setDeviceToken(data.device_token);
}

async function tryAutoPairFromUrl() {
  const params = new URLSearchParams(location.search);
  const pin = params.get("pair_pin");
  if (!pin) return false;
  try {
    await attemptPairing(pin, "Phone (paired via QR)");
    history.replaceState(null, "", location.pathname);
    return true;
  } catch (err) {
    history.replaceState(null, "", location.pathname);
    showPairingGate(`Couldn't pair automatically (${err.message}). Enter the PIN manually below.`);
    return false;
  }
}

async function reloadAfterPairing() {
  hidePairingGate();
  await loadPersonas();
  await loadModels();
  await loadConversations();
  await refreshStatus();
}

el("pairingGateForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pin = el("pairingGatePin").value.trim();
  const name = el("pairingGateName").value.trim() || guessDeviceName();
  const errEl = el("pairingGateError");
  const btn = el("pairingGateSubmit");
  if (!/^\d{4,8}$/.test(pin)) {
    errEl.textContent = "Enter the 6-digit PIN shown in Settings → Phone pairing.";
    return;
  }
  btn.disabled = true;
  errEl.textContent = "";
  try {
    await attemptPairing(pin, name);
    await reloadAfterPairing();
  } catch (err) {
    errEl.textContent = err.message || "Pairing failed.";
  } finally {
    btn.disabled = false;
  }
});

// ---------- Logs modal ----------

el("logsBtn").addEventListener("click", async () => {
  closeMobileSidebar();
  let logs;
  try {
    logs = await api("/api/logs");
  } catch (err) {
    if (err.pairingRequired) return;
    alert(`Could not load logs: ${err.message}`);
    return;
  }
  const list = el("logsList");
  list.innerHTML = "";
  if (!logs.length) {
    list.innerHTML = '<p class="muted small">No requests logged yet.</p>';
  }
  logs.forEach((entry) => {
    const div = document.createElement("div");
    div.className = "log-entry" + (entry.error ? " log-error" : "");
    const date = new Date(entry.timestamp * 1000).toLocaleString();
    div.innerHTML = `
      <div class="log-meta">${date} · ${entry.persona || "?"}${entry.model_used ? ` · ${entry.model_used}` : ""}${entry.latency_ms ? ` · ${entry.latency_ms} ms` : ""}</div>
      <div class="log-message"><strong>You:</strong> ${escapeHtml(entry.message || "")}</div>
      <div class="log-reply">${entry.error ? "Error: " + escapeHtml(entry.error) : escapeHtml(entry.reply || "")}</div>
    `;
    list.appendChild(div);
  });
  openModal("logsModal");
});

el("clearLogsBtn").addEventListener("click", async () => {
  await api("/api/logs", { method: "DELETE" });
  el("logsList").innerHTML = '<p class="muted small">No requests logged yet.</p>';
});

async function init() {
  if (new URLSearchParams(location.search).has("pair_pin")) {
    await tryAutoPairFromUrl();
  }
  loadPersonas();
  loadModels();
  loadConversations();
  refreshStatus();
  maybeAutoCheckUpdatesOnStartup();
  setInterval(refreshStatus, 15000);
}
init();
