const state = {
  personas: [],
  models: [],
  conversations: [],
  activePersona: null,
  conversationId: null, // null = unsaved new chat; set once the first message round-trips
  modelOverride: null,  // null = use persona's own base model
};

const el = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await resp.json().catch(() => ({}));
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
  } catch {
    pill.className = "status-pill status-down";
    text.textContent = "Status check failed";
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
    info.innerHTML = `<span class="model-name">${escapeHtml(entry.name)}</span>${badge}<span class="model-detail">${escapeHtml(entry.family)} · ${escapeHtml(entry.parameter_size)} · ~${escapeHtml(entry.approx_size)}</span>`;

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
  const q = (el("catalogSearch").value || "").trim().toLowerCase();
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
    const info = document.createElement("span");
    info.innerHTML = `<span class="model-name">${escapeHtml(m.name)}</span><span class="model-detail">${escapeHtml([m.parameter_size, m.quantization, m.size_human].filter(Boolean).join(" · "))}</span>`;
    const actions = document.createElement("span");
    actions.className = "model-row-actions";
    const usage = document.createElement("span");
    usage.className = "model-usage-slot";
    usage.textContent = "—";
    usage.title = "Token usage";
    const btn = document.createElement("button");
    btn.className = "icon-btn model-update-btn";
    btn.dataset.name = m.name;
    btn.title = "Check for updates";
    btn.type = "button";
    btn.textContent = "⟳";
    actions.appendChild(usage);
    actions.appendChild(btn);
    row.appendChild(info);
    row.appendChild(actions);
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

function populateModelSelect() {
  const select = el("modelSelect");
  const persona = state.personas.find((p) => p.id === state.activePersona);
  select.innerHTML = "";
  const defaultOpt = document.createElement("option");
  defaultOpt.value = "";
  defaultOpt.textContent = persona ? `Persona default (${persona.base_model})` : "Persona default";
  select.appendChild(defaultOpt);
  state.models.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m.name;
    opt.textContent = m.name;
    select.appendChild(opt);
  });
  select.value = state.modelOverride || "";
}

el("modelSelect").addEventListener("change", (e) => {
  state.modelOverride = e.target.value || null;
  updateChatHeader();
  closePersonaPicker();
});

// ---------- Personas / header picker ----------

function isEmptyThread() {
  return !el("messages").querySelector(".msg-row");
}

function currentPersona() {
  return state.personas.find((p) => p.id === state.activePersona) || state.personas[0] || null;
}

function renderPersonaPicker() {
  const list = el("personaPickerList");
  list.innerHTML = "";
  state.personas.forEach((p) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "picker-item" + (p.id === state.activePersona ? " active" : "");
    btn.innerHTML = `<span class="persona-name">${escapeHtml(p.id)}</span><span class="persona-model">${escapeHtml(p.base_model || "parse error")}</span>`;
    btn.addEventListener("click", () => setPersona(p.id));
    list.appendChild(btn);
  });
}

async function loadPersonas() {
  state.personas = await api("/api/personas");
  if (!state.activePersona && state.personas.length) {
    state.activePersona = state.personas[0].id;
  }
  renderPersonaPicker();
  populateModelSelect();
  updateChatHeader();
  if (isEmptyThread()) {
    const persona = currentPersona();
    el("messages").innerHTML = "";
    el("messages").appendChild(emptyStateNode(persona));
  }
}

function setPersona(id) {
  if (state.activePersona !== id) {
    state.activePersona = id;
    state.modelOverride = null;
  }
  renderPersonaPicker();
  populateModelSelect();
  updateChatHeader();
  const persona = state.personas.find((p) => p.id === id);
  if (isEmptyThread()) {
    el("messages").innerHTML = "";
    el("messages").appendChild(emptyStateNode(persona));
  }
}

function isPersonaPickerOpen() {
  return !el("personaPickerMenu").classList.contains("hidden");
}

function closePersonaPicker() {
  el("personaPickerMenu").classList.add("hidden");
  el("personaPickerBtn").setAttribute("aria-expanded", "false");
}

function togglePersonaPicker() {
  if (isPersonaPickerOpen()) {
    closePersonaPicker();
    return;
  }
  closeMobileSidebar();
  el("personaPickerMenu").classList.remove("hidden");
  el("personaPickerBtn").setAttribute("aria-expanded", "true");
}

el("personaPickerBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  togglePersonaPicker();
});

el("personaPickerMenu").addEventListener("click", (e) => {
  e.stopPropagation();
});

function isMobileLayout() {
  return window.matchMedia("(max-width: 859px)").matches;
}

function closeMobileSidebar() {
  document.body.classList.remove("sidebar-open");
  el("menuBtn").setAttribute("aria-expanded", "false");
  el("menuBtn").setAttribute("aria-label", "Open chats");
}

function openMobileSidebar() {
  closePersonaPicker();
  document.body.classList.add("sidebar-open");
  el("menuBtn").setAttribute("aria-expanded", "true");
  el("menuBtn").setAttribute("aria-label", "Close chats");
}

function toggleMobileSidebar() {
  if (document.body.classList.contains("sidebar-open")) closeMobileSidebar();
  else openMobileSidebar();
}

el("menuBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  toggleMobileSidebar();
});
el("sidebarBackdrop").addEventListener("click", closeMobileSidebar);

window.addEventListener("resize", () => {
  if (!isMobileLayout()) closeMobileSidebar();
});

function updateChatHeader() {
  const persona = state.personas.find((p) => p.id === state.activePersona);
  el("chatPersonaName").textContent = persona ? persona.id : "Pick a persona";
  el("chatModelBadge").textContent = state.modelOverride || (persona ? persona.base_model : "—");
}

function emptyStateNode(persona) {
  const div = document.createElement("div");
  div.className = "empty-state";
  const h1 = document.createElement("h1");
  h1.textContent = persona ? persona.id : "Pick a persona and say hello";
  const p = document.createElement("p");
  p.className = "muted";
  p.textContent = persona ? persona.system_preview : "";
  div.appendChild(h1);
  div.appendChild(p);
  return div;
}

function renderEmptyState(persona) {
  el("personaHeading").textContent = persona ? persona.id : "Pick a persona and say hello";
  el("personaSubheading").textContent = persona ? persona.system_preview : "";
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
    appendMessage("error", err.message);
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

async function startNewChat() {
  closeMobileSidebar();
  closePersonaPicker();
  const persona = currentPersona();

  if (isEmptyThread() && state.conversationId) {
    el("input").focus();
    return;
  }

  state.conversationId = null;
  el("messages").innerHTML = "";
  el("messages").appendChild(emptyStateNode(persona));
  updateChatHeader();
  loadConversations();

  if (persona) {
    try {
      const data = await api("/api/conversations", {
        method: "POST",
        body: JSON.stringify({
          persona: persona.id,
          model_override: state.modelOverride,
        }),
      });
      state.conversationId = data.id;
    } catch {
      state.conversationId = null;
    }
  }
  await loadConversations();
  el("input").focus();
}

el("newChatBtn").addEventListener("click", () => startNewChat());
el("headerNewChatBtn").addEventListener("click", () => startNewChat());

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
    titleBtn.innerHTML = `<span>${escapeHtml(c.title || "New chat")}</span><span class="conversation-meta">${escapeHtml(c.persona)} · ${timeAgo(c.updated_at)}</span>`;

    const actions = document.createElement("div");
    actions.className = "conversation-actions";
    actions.innerHTML = `
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
  let conv;
  try {
    conv = await api(`/api/conversations/${id}`);
  } catch (err) {
    appendMessage("error", err.message);
    return;
  }
  state.conversationId = id;
  state.activePersona = conv.persona;
  const persona = state.personas.find((p) => p.id === conv.persona);
  state.modelOverride = persona && conv.model_used !== persona.base_model ? conv.model_used : null;

  closeMobileSidebar();
  closePersonaPicker();
  renderPersonaPicker();
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

    if (action === "rename") {
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
          el("messages").innerHTML = "";
          el("messages").appendChild(emptyStateNode(state.personas.find((p) => p.id === state.activePersona)));
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

const SETTINGS_PANE_TITLES = {
  general: "General",
  installed: "Installed models",
  download: "Download",
  updates: "Updates",
  pairing: "Phone pairing",
};

function showSettingsPane(pane) {
  document.querySelectorAll(".settings-nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.pane === pane);
  });
  document.querySelectorAll(".settings-pane").forEach((section) => {
    section.classList.toggle("hidden", section.id !== `pane-${pane}`);
  });
  el("settingsPaneTitle").textContent = SETTINGS_PANE_TITLES[pane] || pane;
  el("settingsFooter").classList.toggle("hidden", pane !== "general" && pane !== "updates");
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
function closeModal(id) { el(id).classList.add("hidden"); }

document.querySelectorAll("[data-close]").forEach((btn) => {
  btn.addEventListener("click", () => closeModal(btn.dataset.close));
});

document.querySelectorAll(".modal-backdrop").forEach((backdrop) => {
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) closeModal(backdrop.id);
  });
});

document.addEventListener("click", () => {
  closePersonaPicker();
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  closePersonaPicker();
  closeMobileSidebar();
  ["settingsModal", "logsModal"].forEach((id) => {
    if (!el(id).classList.contains("hidden")) closeModal(id);
  });
});

document.querySelectorAll(".settings-nav-item").forEach((btn) => {
  btn.addEventListener("click", () => showSettingsPane(btn.dataset.pane));
});

el("catalogSearch").addEventListener("input", filterCatalogTable);
el("settingsSearch").addEventListener("input", filterSettingsNav);

el("settingsBtn").addEventListener("click", async () => {
  closeMobileSidebar();
  const settings = await api("/api/settings");
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
  if (el("autoCheckUpdatesInput").checked) {
    runUpdateCheck({ silent: true });
  } else {
    setUpdateDot(false);
  }
});

// ---------- Update checking (opt-in, off by default) ----------

function setUpdateDot(show) {
  el("settingsUpdateDot").classList.toggle("hidden", !show);
  el("settingsNavUpdateDot").classList.toggle("hidden", !show);
}

async function persistUpdateSettings() {
  await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      update_check_url: el("updateUrlInput").value.trim(),
      auto_check_updates: el("autoCheckUpdatesInput").checked,
    }),
  });
}

async function runUpdateCheck({ silent = false } = {}) {
  if (!silent) {
    await persistUpdateSettings();
    el("updateStatusText").textContent = "Checking…";
  }
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

async function loadPairingInfo() {
  try {
    const data = await api("/api/pairing/pin");
    el("pairingPinText").textContent = data.pin;
    el("lanUrlText").textContent = data.lan_url;
  } catch (err) {
    el("pairingPinText").textContent = "error";
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

// ---------- Logs modal ----------

el("logsBtn").addEventListener("click", async () => {
  closeMobileSidebar();
  const logs = await api("/api/logs");
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

// ---------- Init ----------

loadPersonas();
loadModels();
loadConversations();
refreshStatus();
maybeAutoCheckUpdatesOnStartup();
setInterval(refreshStatus, 15000);
