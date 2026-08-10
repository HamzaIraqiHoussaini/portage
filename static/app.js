const SESSION_KEY = "portage.sessionTokens";

const state = {
  chatId: null,
  chatSource: null,
  transcriptPath: null,
  chats: [],
  skills: [],
  provider: "foundry",
  connected: false,
  slashIndex: 0,
  sourceFilter: "all",
  usageLast: null,
  usageChat: null,
  usageSession: loadSessionUsage(),
};

const MATH_DELIMITERS = [
  { left: "$$", right: "$$", display: true },
  { left: "\\[", right: "\\]", display: true },
  { left: "\\(", right: "\\)", display: false },
  { left: "$", right: "$", display: false },
];

const SOURCE_LABELS = {
  local: "Local",
  cursor: "Cursor",
  "claude-code": "Claude Code",
  chatgpt: "ChatGPT",
  antigravity: "Antigravity",
  import: "Import",
};

const $ = (id) => document.getElementById(id);

function on(id, event, handler) {
  const el = $(id);
  if (!el) {
    console.warn(`Missing #${id}; skipped ${event} handler`);
    return;
  }
  el.addEventListener(event, handler);
}

function loadSessionUsage() {
  try {
    const raw = JSON.parse(localStorage.getItem(SESSION_KEY) || "{}");
    return {
      input_tokens: Number(raw.input_tokens) || 0,
      output_tokens: Number(raw.output_tokens) || 0,
      total_tokens: Number(raw.total_tokens) || 0,
    };
  } catch {
    return { input_tokens: 0, output_tokens: 0, total_tokens: 0 };
  }
}

function saveSessionUsage() {
  localStorage.setItem(SESSION_KEY, JSON.stringify(state.usageSession));
}

function formatTokens(usage, { zeroOk = false } = {}) {
  if (!usage) return zeroOk ? "0 → 0 · 0" : "—";
  const inp = Number(usage.input_tokens) || 0;
  const out = Number(usage.output_tokens) || 0;
  const total = Number(usage.total_tokens) || inp + out;
  if (!total && !inp && !out) return zeroOk ? "0 → 0 · 0" : "—";
  return `${fmt(inp)} → ${fmt(out)} · ${fmt(total)}`;
}

function fmt(n) {
  return new Intl.NumberFormat("en", { notation: n >= 10000 ? "compact" : "standard" }).format(n);
}

function updateTokenMeter() {
  if ($("token-last")) $("token-last").textContent = formatTokens(state.usageLast);
  if ($("token-chat")) $("token-chat").textContent = formatTokens(state.usageChat);
  if ($("token-session")) $("token-session").textContent = formatTokens(state.usageSession, { zeroOk: true });
  const meter = $("token-meter");
  if (meter) meter.hidden = !state.chatId;
}

function chatKey(chat) {
  return `${chat.source || "local"}::${chat.id}::${chat.transcript_path || ""}`;
}

function activeChatKey() {
  return `${state.chatSource || "local"}::${state.chatId || ""}::${state.transcriptPath || ""}`;
}

function addSessionUsage(usage) {
  if (!usage) return;
  state.usageLast = {
    input_tokens: Number(usage.input_tokens) || 0,
    output_tokens: Number(usage.output_tokens) || 0,
    total_tokens: Number(usage.total_tokens) || 0,
  };
  for (const key of ["input_tokens", "output_tokens", "total_tokens"]) {
    state.usageSession[key] = (Number(state.usageSession[key]) || 0) + (Number(usage[key]) || 0);
  }
  saveSessionUsage();
  updateTokenMeter();
}

function isMobile() {
  return window.matchMedia("(max-width: 960px)").matches;
}

function showChatView(show) {
  const shell = $("app-shell");
  const back = $("mobile-back");
  if (show) {
    shell.classList.add("show-chat");
    back.hidden = !isMobile();
  } else {
    shell.classList.remove("show-chat");
    back.hidden = true;
  }
}

function formatDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (typeof d === "string") return d;
        if (d && typeof d === "object") return d.msg || JSON.stringify(d);
        return String(d);
      })
      .join("; ");
  }
  if (detail && typeof detail === "object") return detail.msg || JSON.stringify(detail);
  return "Request failed";
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (opts.body && !(opts.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { ...opts, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(formatDetail(data.detail) || data.message || res.statusText);
  return data;
}

function setConnectStatus(text, kind = "") {
  const el = $("connect-status");
  el.textContent = text;
  el.className = `status ${kind}`.trim();
}

function setComposerEnabled(enabled, { connected = state.connected } = {}) {
  const canSend = enabled && connected;
  $("message-input").disabled = !canSend;
  $("send-btn").disabled = !canSend;
  $("composer")?.classList.toggle("is-idle", !canSend);
  if (!enabled) {
    $("composer-hint").textContent = "Select a chat or start a new one";
  } else if (!connected) {
    $("composer-hint").textContent = "Connect a provider in Settings to send";
  } else {
    $("composer-hint").textContent =
      "Enter to send · / for skills · Shift+Enter newline · markdown & LaTeX";
  }
}

function setProviderCollapsed(collapsed) {
  const body = $("provider-body");
  const chip = $("provider-chip-settings");
  const toggle = $("provider-toggle");
  if (!body) return;
  body.hidden = collapsed;
  if (chip) chip.hidden = !collapsed || !state.connected;
  if (toggle) {
    // Keep Edit/Done visible whenever connected so the form can be collapsed again.
    toggle.hidden = !state.connected;
    toggle.textContent = collapsed ? "Edit" : "Done";
  }
}

function syncProviderChips(text, { ok = false } = {}) {
  const label = text || "Not connected · Settings";
  const rail = $("provider-chip");
  if (rail) {
    rail.textContent = label;
    rail.hidden = false;
    rail.classList.toggle("ok", !!ok);
  }
  const settingsChip = $("provider-chip-settings");
  if (settingsChip) {
    settingsChip.textContent = label;
    // Visibility follows collapse state when connected
    if (!state.connected) settingsChip.hidden = true;
  }
}

const THEME_KEY = "portage-theme";

function currentTheme() {
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "dark" || attr === "light") return attr;
  return "light";
}

function storedTheme() {
  try {
    const t = localStorage.getItem(THEME_KEY);
    if (t === "dark" || t === "light") return t;
  } catch {
    /* ignore */
  }
  return null;
}

function setThemeIcons(theme) {
  const dark = theme === "dark";
  document.querySelectorAll(".theme-icon-moon").forEach((el) => {
    el.hidden = dark;
  });
  document.querySelectorAll(".theme-icon-sun").forEach((el) => {
    el.hidden = !dark;
  });
  for (const id of ["theme-toggle", "theme-toggle-stage"]) {
    const toggle = $(id);
    if (!toggle) continue;
    toggle.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    toggle.title = dark ? "Light mode" : "Dark mode";
  }
}

function applyTheme(theme, { persist = true } = {}) {
  const next = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  if (persist) {
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* ignore */
    }
  }
  document.querySelectorAll(".theme-btn").forEach((btn) => {
    const on = btn.dataset.themeChoice === next;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  setThemeIcons(next);
}

let settingsLastFocus = null;

function settingsFocusables() {
  const sheet = $("settings-sheet");
  if (!sheet) return [];
  return [...sheet.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])")].filter(
    (el) => !el.disabled && !el.hidden && el.offsetParent !== null && el.getAttribute("aria-hidden") !== "true"
  );
}

function onSettingsKeydown(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    closeSettings();
    return;
  }
  if (event.key !== "Tab") return;
  const nodes = settingsFocusables();
  if (!nodes.length) return;
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function openSettings({ focusConnect = false } = {}) {
  const sheet = $("settings-sheet");
  const backdrop = $("settings-backdrop");
  const shell = $("app-shell");
  if (!sheet || !backdrop) return;
  settingsLastFocus = document.activeElement;
  sheet.hidden = false;
  backdrop.hidden = false;
  document.body.style.overflow = "hidden";
  if (shell) shell.setAttribute("aria-hidden", "true");
  $("settings-open")?.setAttribute("aria-expanded", "true");
  $("settings-open-stage")?.setAttribute("aria-expanded", "true");
  document.addEventListener("keydown", onSettingsKeydown);
  if (focusConnect) {
    setProviderCollapsed(false);
    requestAnimationFrame(() => {
      $("connect-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
      ($("connect-btn") || $("settings-close"))?.focus();
    });
  } else {
    requestAnimationFrame(() => $("settings-close")?.focus());
  }
}

function closeSettings() {
  const sheet = $("settings-sheet");
  const backdrop = $("settings-backdrop");
  const shell = $("app-shell");
  if (sheet) sheet.hidden = true;
  if (backdrop) backdrop.hidden = true;
  document.body.style.overflow = "";
  if (shell) shell.removeAttribute("aria-hidden");
  $("settings-open")?.setAttribute("aria-expanded", "false");
  $("settings-open-stage")?.setAttribute("aria-expanded", "false");
  document.removeEventListener("keydown", onSettingsKeydown);
  const restore = settingsLastFocus;
  settingsLastFocus = null;
  if (restore && typeof restore.focus === "function") {
    try {
      restore.focus();
    } catch {
      $("settings-open")?.focus();
    }
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function extractTools(text) {
  const tools = [];
  let cleaned = String(text || "");
  cleaned = cleaned.replace(/\[tool_use:([^\]]+)\]/g, (_, name) => {
    tools.push(String(name).trim());
    return "";
  });
  cleaned = cleaned.replace(/\[tool_result\]/g, () => {
    tools.push("result");
    return "";
  });
  cleaned = cleaned.replace(/\n{3,}/g, "\n\n").trim();
  const unique = [];
  for (const t of tools) if (t && !unique.includes(t)) unique.push(t);
  return { text: cleaned, tools: unique };
}

function protectMath(text) {
  const slots = [];
  const patterns = [
    /\$\$[\s\S]+?\$\$/g,
    /\\\[[\s\S]+?\\\]/g,
    /\\\([\s\S]+?\\\)/g,
    /(?<!\$)\$(?!\$)(?:\\\$|[^$\n])+?\$(?!\$)/g,
  ];
  let out = String(text);
  for (const re of patterns) {
    out = out.replace(re, (match) => {
      const idx = slots.length;
      slots.push(match);
      return `@@MATH${idx}@@`;
    });
  }
  return { text: out, slots };
}

function restoreMath(html, slots) {
  return html.replace(/@@MATH(\d+)@@/g, (_, idx) => slots[Number(idx)] || "");
}

function formatMessageHtml(text) {
  const raw = String(text || "");
  if (!raw.trim()) return "";
  if (typeof DOMPurify === "undefined") {
    return `<p>${escapeHtml(raw).replaceAll("\n", "<br>")}</p>`;
  }
  const { text: protectedText, slots } = protectMath(raw);
  let html =
    typeof marked !== "undefined" && marked.parse
      ? marked.parse(protectedText, { gfm: true, breaks: true })
      : `<p>${escapeHtml(protectedText).replaceAll("\n", "<br>")}</p>`;
  html = restoreMath(html, slots);
  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    ADD_ATTR: ["class", "open"],
    FORBID_TAGS: ["style", "iframe", "object", "embed", "form", "input", "button"],
    FORBID_ATTR: ["style", "onerror", "onclick", "onload"],
  });
}

function renderMath(root) {
  if (typeof renderMathInElement !== "function") return;
  try {
    renderMathInElement(root, { delimiters: MATH_DELIMITERS, throwOnError: false, strict: "ignore" });
  } catch (err) {
    console.warn("KaTeX render failed", err);
  }
}

function createToolSummary(tools) {
  const details = document.createElement("details");
  details.className = "tool-summary";
  const summary = document.createElement("summary");
  const visible = tools.filter((t) => t !== "result");
  summary.textContent =
    visible.length > 0
      ? `Used ${visible.length} tool${visible.length === 1 ? "" : "s"}: ${visible.slice(0, 6).join(", ")}${visible.length > 6 ? "…" : ""}`
      : "Tool activity";
  details.appendChild(summary);
  const list = document.createElement("ul");
  for (const name of visible) {
    const li = document.createElement("li");
    li.textContent = name;
    list.appendChild(li);
  }
  details.appendChild(list);
  return details;
}

function createBubble(role, text) {
  const { text: cleaned, tools } = extractTools(text);
  if (!cleaned && tools.length === 0) return null;
  const div = document.createElement("article");
  div.className = `bubble ${role === "error" ? "error" : role}`;
  if (!cleaned && tools.length) div.classList.add("tools-only");
  const roleEl = document.createElement("span");
  roleEl.className = "role";
  roleEl.textContent = role;
  div.appendChild(roleEl);
  if (tools.length) div.appendChild(createToolSummary(tools));
  if (cleaned) {
    const body = document.createElement("div");
    body.className = "bubble-body";
    body.innerHTML = formatMessageHtml(cleaned);
    renderMath(body);
    div.appendChild(body);
  }
  return div;
}

function setProviderUI(provider) {
  state.provider = provider;
  document.querySelectorAll(".seg-btn").forEach((btn) => {
    const on = btn.dataset.provider === provider;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  const foundry = $("foundry-fields");
  const aws = $("aws-fields");
  if (foundry) foundry.hidden = provider !== "foundry";
  if (aws) aws.hidden = provider !== "aws";
}

function renderWorkspaces(list) {
  const root = $("workspace-list");
  const select = $("workspace-select");
  const current = select.value;
  root.innerHTML = "";
  select.innerHTML = `<option value="">None</option>`;
  for (const ws of list || []) {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.innerHTML = `<span title="${escapeHtml(ws.path)}">${escapeHtml(ws.name)}${ws.exists ? "" : " (missing)"}</span>`;
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "ghost tiny";
    rm.textContent = "×";
    rm.addEventListener("click", () => removeWorkspace(ws.path));
    chip.appendChild(rm);
    root.appendChild(chip);

    const opt = document.createElement("option");
    opt.value = ws.path;
    opt.textContent = ws.name;
    select.appendChild(opt);
  }
  if ([...select.options].some((o) => o.value === current)) select.value = current;
}

async function loadSkills() {
  const data = await api("/api/skills");
  state.skills = data.skills || [];
}

async function loadStatus() {
  const s = await api("/api/status");
  setProviderUI(s.provider || "foundry");
  state.connected = !!s.connected;
  if (s.messages_url && !s.messages_url.includes("YOUR-RESOURCE")) {
    $("messages-url").value = s.messages_url;
    $("messages-url").title = s.messages_url;
  }
  if (s.model && s.provider !== "aws") $("model").value = s.model;
  if (s.aws_region) $("aws-region").value = s.aws_region;
  if (s.aws_model_id) $("aws-model").value = s.aws_model_id;

  $("writeback-toggle").checked = !!s.writeback_enabled;
  $("cursor-link-toggle").checked = !!s.cursor_link_enabled;

  const cursor = s.cursor || {};
  if (!cursor.detected) {
    $("cursor-status").textContent = "Not detected";
  } else if (s.cursor_active) {
    $("cursor-status").textContent = `Linked · ~${cursor.transcript_hint_count || 0}`;
  } else {
    $("cursor-status").textContent = "Detected · link off";
  }

  const claude = s.claude_code || {};
  const claudeToggle = $("claude-code-toggle");
  if (claudeToggle) {
    if (!claude.detected) {
      claudeToggle.checked = false;
      claudeToggle.disabled = true;
      $("claude-status").textContent = "Not detected";
      $("claude-card")?.classList.add("disabled");
    } else {
      claudeToggle.disabled = false;
      claudeToggle.checked = s.claude_code_link_enabled !== false;
      $("claude-card")?.classList.remove("disabled");
      $("claude-status").textContent = s.claude_code_active
        ? `Linked · ~${claude.transcript_hint_count || 0}`
        : "Detected · link off";
    }
  }

  const anti = s.antigravity || {};
  if ($("antigravity-status")) {
    $("antigravity-status").textContent = anti.detected
      ? `~${anti.transcript_hint_count || 0} encrypted · click to import export`
      : "Click to import JSON / Markdown export";
  }

  $("skills-meta").textContent = `Skills imported: ${s.skills_count}`;
  $("skills-meta").title = (s.priority_skills || []).join(", ");
  $("settings-meta").textContent = s.cursor_active
    ? "Cursor settings available for context"
    : "Cursor settings skipped (link off or not detected)";

  const keyNote = $("api-key-note");
  const awsNote = $("aws-key-note");
  if (s.provider === "aws") {
    awsNote.hidden = !s.has_key;
    $("aws-secret-key").placeholder = s.has_key
      ? "Secret saved — enter to replace"
      : "AWS secret access key";
  } else {
    keyNote.hidden = !s.has_key;
    $("api-key").placeholder = s.has_key
      ? "Key saved on server — enter to replace"
      : "Azure / Foundry key";
  }

  renderWorkspaces(s.workspaces || []);

  const chipText = s.connected ? `Connected · ${s.provider} · ${s.model}` : "Not connected · Settings";
  if (s.connected) {
    setConnectStatus(`Connected · ${s.provider} · ${s.model}`, "ok");
    syncProviderChips(chipText, { ok: true });
    setProviderCollapsed(true);
  } else if (s.has_key) {
    setConnectStatus("Credentials saved — test connection", "");
    syncProviderChips("Credentials saved · Settings", { ok: false });
    setProviderCollapsed(false);
  } else {
    setConnectStatus("Not connected", "");
    syncProviderChips("Not connected · Settings", { ok: false });
    setProviderCollapsed(false);
  }

  if (state.chatId) setComposerEnabled(true);
  updateEmptyStage();
}

async function connect() {
  const btn = $("connect-btn");
  btn.disabled = true;
  setConnectStatus("Testing connection…");
  try {
    const provider = state.provider;
    const body =
      provider === "aws"
        ? {
            provider: "aws",
            aws_region: $("aws-region").value.trim(),
            aws_access_key_id: $("aws-access-key").value.trim(),
            aws_secret_access_key: $("aws-secret-key").value.trim(),
            aws_model_id: $("aws-model").value.trim(),
            cursor_link_enabled: $("cursor-link-toggle").checked,
            claude_code_link_enabled: $("claude-code-toggle")?.checked !== false,
            writeback_enabled: $("writeback-toggle").checked,
          }
        : {
            provider: "foundry",
            foundry_messages_url: $("messages-url").value.trim(),
            foundry_api_key: $("api-key").value.trim(),
            foundry_model: $("model").value.trim() || "claude-opus-5",
            anthropic_version: "2023-06-01",
            cursor_link_enabled: $("cursor-link-toggle").checked,
            claude_code_link_enabled: $("claude-code-toggle")?.checked !== false,
            writeback_enabled: $("writeback-toggle").checked,
          };
    const session = $("aws-session").value.trim();
    if (provider === "aws" && session) body.aws_session_token = session;
    const result = await api("/api/connect", { method: "POST", body: JSON.stringify(body) });
    setConnectStatus(`Connected · reply: ${result.test.reply}`, "ok");
    $("api-key").value = "";
    $("aws-secret-key").value = "";
    $("aws-session").value = "";
    await loadStatus();
  } catch (err) {
    setConnectStatus(String(err.message || err), "err");
  } finally {
    btn.disabled = false;
  }
}

async function toggleCursorLink() {
  try {
    await api("/api/cursor-link", {
      method: "POST",
      body: JSON.stringify({ enabled: $("cursor-link-toggle").checked }),
    });
    await loadStatus();
    await loadChats();
  } catch (err) {
    setConnectStatus(String(err.message || err), "err");
  }
}

async function toggleClaudeCodeLink() {
  try {
    await api("/api/claude-code-link", {
      method: "POST",
      body: JSON.stringify({ enabled: $("claude-code-toggle").checked }),
    });
    await loadStatus();
    await loadChats();
  } catch (err) {
    setConnectStatus(String(err.message || err), "err");
  }
}

async function toggleWriteback() {
  try {
    await api("/api/writeback", {
      method: "POST",
      body: JSON.stringify({ enabled: $("writeback-toggle").checked }),
    });
    await loadStatus();
  } catch (err) {
    setConnectStatus(String(err.message || err), "err");
  }
}

async function addWorkspace() {
  const path = $("workspace-path").value.trim();
  if (!path) return;
  try {
    const data = await api("/api/workspaces", { method: "POST", body: JSON.stringify({ path }) });
    $("workspace-path").value = "";
    renderWorkspaces(data.workspaces || []);
    $("workspace-select").value = data.workspace.path;
  } catch (err) {
    setConnectStatus(String(err.message || err), "err");
  }
}

async function removeWorkspace(path) {
  try {
    const data = await api(`/api/workspaces?path=${encodeURIComponent(path)}`, {
      method: "DELETE",
    });
    renderWorkspaces(data.workspaces || []);
  } catch (err) {
    setConnectStatus(String(err.message || err), "err");
  }
}

async function importFile(file) {
  const status = $("import-status");
  status.textContent = `Importing ${file.name}…`;
  status.className = "status";
  try {
    const form = new FormData();
    form.append("file", file);
    const data = await api("/api/import", { method: "POST", body: form });
    status.textContent = `Imported ${data.imported} conversation${data.imported === 1 ? "" : "s"}.`;
    status.className = "status ok";
    await loadChats();
    if (data.chats?.length) {
      await openChat(data.chats[0].id, data.chats[0].source || "local");
    }
  } catch (err) {
    status.textContent = String(err.message || err);
    status.className = "status err";
  }
}

function sourceLabel(source) {
  return SOURCE_LABELS[source] || source || "local";
}

function renderChatList() {
  const root = $("chat-list");
  root.innerHTML = "";
  const filtered = state.chats.filter((chat) => {
    if (state.sourceFilter === "all") return true;
    const src = chat.source || "local";
    if (state.sourceFilter === "local") return src === "local" || src === "import";
    return src === state.sourceFilter;
  });
  if (!filtered.length) {
    root.innerHTML = `<p class="empty">No conversations yet. Start a new one, link Cursor / Claude Code, or import ChatGPT / Antigravity.</p>`;
    return;
  }
  const active = activeChatKey();
  for (const chat of filtered) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `chat-item${chatKey(chat) === active ? " active" : ""}`;
    const src = chat.source || "local";
    const project = chat.project || chat.workspace || "";
    btn.innerHTML = `
      <span class="t">${escapeHtml(chat.title || chat.id)}</span>
      <span class="p">${escapeHtml(sourceLabel(src))}${project ? ` · ${escapeHtml(project)}` : ""}</span>
      <span class="prev">${escapeHtml(chat.preview || "")}</span>
    `;
    btn.addEventListener("click", () => {
      openChat(chat.id, src, chat.transcript_path || null).catch((err) =>
        setConnectStatus(String(err.message || err), "err")
      );
    });
    root.appendChild(btn);
  }
}

async function loadChats() {
  const data = await api("/api/chats");
  state.chats = data.chats || [];
  renderChatList();
  updateEmptyStage();
}

async function newChat() {
  const workspace = $("workspace-select").value || null;
  const chat = await api("/api/chats/new", {
    method: "POST",
    body: JSON.stringify({ title: "New conversation", workspace }),
  });
  await loadChats();
  await openChat(chat.id, "local", null);
}

function updateEmptyStage() {
  const empty = $("empty-stage");
  if (!empty) return;
  empty.hidden = !!state.chatId;
  const lead = $("empty-lead");
  const connectBtn = $("empty-connect");
  const meter = $("token-meter");
  const note = $("sync-note");
  if (meter) meter.hidden = !state.chatId;
  if (note && !state.chatId) {
    note.hidden = true;
    note.textContent = "";
  }
  if (state.chatId) return;

  const hasChats = (state.chats || []).length > 0;
  if (!state.connected) {
    if (lead) {
      lead.textContent = hasChats
        ? "Connect a provider to send — you can still open chats to read them."
        : "Connect a provider, then start or import a conversation.";
    }
    if (connectBtn) {
      connectBtn.hidden = false;
      connectBtn.textContent = "Connect provider";
    }
  } else {
    if (lead) {
      lead.textContent = hasChats
        ? "Pick a conversation on the left, or start a new one."
        : "Start a new conversation to begin.";
    }
    if (connectBtn) connectBtn.hidden = true;
  }
}

async function openChat(id, sourceHint, transcriptPath) {
  state.chatId = id;
  state.transcriptPath = transcriptPath || null;
  renderChatList();
  $("messages").setAttribute("aria-busy", "true");
  try {
    let url = `/api/chats/${encodeURIComponent(id)}`;
    if (transcriptPath) url += `?path=${encodeURIComponent(transcriptPath)}`;
    const thread = await api(url);
    state.chatSource = thread.source || sourceHint || "local";
    state.transcriptPath = thread.transcript_path || transcriptPath || null;
    state.usageLast = thread.usage_last || null;
    state.usageChat = thread.usage_total || null;
    updateTokenMeter();
    $("active-title").textContent = thread.title || id;
    $("active-title").title = thread.title || id;
    const project = thread.project || thread.workspace || "";
    $("active-project").textContent = project
      ? `${sourceLabel(state.chatSource)} · ${project}`
      : sourceLabel(state.chatSource);
    if (thread.workspace) $("workspace-select").value = thread.workspace;
    setComposerEnabled(true);
    renderMessages(thread.messages || []);
    showChatView(true);
    updateEmptyStage();
    const note = $("sync-note");
    if (note) {
      note.hidden = false;
      if (state.chatSource === "cursor" && $("writeback-toggle").checked) {
        note.textContent = "Continuing a Cursor chat — replies can write back to the transcript.";
      } else if (state.chatSource === "claude-code") {
        note.textContent = "Claude Code session — first reply continues as a local copy.";
      } else if (state.chatSource === "chatgpt" || state.chatSource === "antigravity") {
        note.textContent = `Imported from ${sourceLabel(state.chatSource)} — continue here on Foundry or Bedrock.`;
      } else {
        note.textContent = "Type / to invoke a skill.";
      }
    }
  } finally {
    $("messages").removeAttribute("aria-busy");
  }
}

function renderMessages(messages) {
  const root = $("messages");
  root.innerHTML = "";
  if (!messages.length) {
    root.innerHTML = `<p class="empty">Start typing — use / to pick a skill.</p>`;
    return;
  }
  for (const m of messages) {
    if (!m.text || !String(m.text).trim()) continue;
    const bubble = createBubble(m.role, m.text);
    if (bubble) root.appendChild(bubble);
  }
  root.scrollTop = root.scrollHeight;
}

function slashQuery(value) {
  const m = value.match(/(^|\n)\/([^\s]*)$/);
  if (!m) return null;
  return m[2].toLowerCase();
}

function filteredSkills(query) {
  const q = (query || "").toLowerCase();
  return state.skills
    .filter((s) => !q || s.name.toLowerCase().includes(q) || (s.description || "").toLowerCase().includes(q))
    .slice(0, 12);
}

function hideSlashMenu() {
  const menu = $("slash-menu");
  if (!menu) return;
  menu.hidden = true;
  menu.innerHTML = "";
}

function showSlashMenu(query) {
  const menu = $("slash-menu");
  const items = filteredSkills(query);
  if (!items.length) {
    hideSlashMenu();
    return;
  }
  state.slashIndex = Math.min(state.slashIndex, items.length - 1);
  menu.hidden = false;
  menu.innerHTML = items
    .map(
      (s, i) => `
      <button type="button" class="slash-item${i === state.slashIndex ? " active" : ""}" data-name="${escapeHtml(s.name)}">
        <strong>/${escapeHtml(s.name)}</strong>
        <span>${escapeHtml((s.description || "").slice(0, 90))}</span>
      </button>`
    )
    .join("");
  menu.querySelectorAll(".slash-item").forEach((btn) => {
    btn.addEventListener("click", () => applySlash(btn.dataset.name));
  });
}

function applySlash(name) {
  const input = $("message-input");
  const value = input.value;
  const replaced = value.replace(/(^|\n)\/[^\s]*$/, `$1/${name} `);
  input.value = replaced;
  hideSlashMenu();
  input.focus();
}

async function sendMessage(event) {
  event.preventDefault();
  if (!state.chatId) return;
  const input = $("message-input");
  const text = input.value.trim();
  if (!text) return;
  hideSlashMenu();

  $("send-btn").disabled = true;
  input.disabled = true;
  const root = $("messages");
  if (root.querySelector(".empty")) root.innerHTML = "";
  const userBubble = createBubble("user", text);
  if (userBubble) root.appendChild(userBubble);
  input.value = "";
  root.scrollTop = root.scrollHeight;

  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        chat_id: state.chatId,
        message: text,
        writeback: $("writeback-toggle").checked,
        source: state.chatSource,
        workspace: $("workspace-select").value || null,
        transcript_path: state.transcriptPath,
      }),
    });
    if (result.forked && result.chat_id) {
      state.chatId = result.chat_id;
      state.chatSource = result.source || "local";
      state.transcriptPath = null;
      $("active-project").textContent = sourceLabel(state.chatSource);
    }
    const replyBubble = createBubble("assistant", result.reply || "");
    if (replyBubble) root.appendChild(replyBubble);
    if (result.usage) {
      addSessionUsage(result.usage);
      state.usageChat = result.usage_total || state.usageChat;
      updateTokenMeter();
    }
    if (result.skill) {
      $("sync-note").textContent =
        `Used skill /${result.skill}` + (result.writeback?.enabled ? " · wrote back to Cursor" : "");
    } else if (result.writeback?.enabled) {
      $("sync-note").textContent = result.writeback.note || "Wrote back into Cursor transcript.";
    } else if (result.forked) {
      $("sync-note").textContent = "Continued as a local conversation (original transcript unchanged).";
    }
    root.scrollTop = root.scrollHeight;
    await loadChats();
  } catch (err) {
    const fail = createBubble("error", String(err.message || err));
    if (fail) root.appendChild(fail);
  } finally {
    setComposerEnabled(true);
    input.focus();
  }
}

document.querySelectorAll(".seg-btn").forEach((btn) => {
  btn.addEventListener("click", () => setProviderUI(btn.dataset.provider));
});

document.querySelectorAll("#source-filter .filter-chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.sourceFilter = btn.dataset.filter || "all";
    document.querySelectorAll("#source-filter .filter-chip").forEach((b) => {
      const on = b === btn;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    renderChatList();
  });
});

on("connect-btn", "click", connect);
on("provider-toggle", "click", () => {
  const body = $("provider-body");
  if (!body) return;
  setProviderCollapsed(!body.hidden);
});
on("settings-open", "click", () => openSettings());
on("settings-close", "click", closeSettings);
on("settings-backdrop", "click", closeSettings);
on("provider-chip", "click", () => openSettings({ focusConnect: !state.connected }));
on("theme-toggle", "click", () => {
  applyTheme(currentTheme() === "dark" ? "light" : "dark", { persist: true });
});
on("theme-toggle-stage", "click", () => {
  applyTheme(currentTheme() === "dark" ? "light" : "dark", { persist: true });
});
document.querySelectorAll(".theme-btn").forEach((btn) => {
  btn.addEventListener("click", () => applyTheme(btn.dataset.themeChoice, { persist: true }));
});
on("settings-open-stage", "click", () => openSettings());
on("cursor-link-toggle", "change", toggleCursorLink);
on("claude-code-toggle", "change", toggleClaudeCodeLink);
on("writeback-toggle", "change", toggleWriteback);
on("workspace-add", "click", addWorkspace);
on("new-chat", "click", () => newChat().catch((e) => setConnectStatus(String(e.message || e), "err")));
on("empty-new", "click", () => newChat().catch((e) => setConnectStatus(String(e.message || e), "err")));
on("empty-connect", "click", () => openSettings({ focusConnect: true }));
on("refresh-chats", "click", () =>
  loadChats().catch((err) => setConnectStatus(String(err.message || err), "err"))
);
on("composer", "submit", sendMessage);
on("mobile-back", "click", () => showChatView(false));
on("import-btn", "click", () => $("import-file")?.click());
on("import-chatgpt-card", "click", () => $("import-file")?.click());
on("import-antigravity-card", "click", () => $("import-file")?.click());
on("import-file", "change", (event) => {
  const file = event.target.files?.[0];
  if (file) importFile(file).finally(() => {
    event.target.value = "";
  });
});

on("message-input", "input", () => {
  const q = slashQuery($("message-input").value);
  if (q === null) hideSlashMenu();
  else {
    state.slashIndex = 0;
    showSlashMenu(q);
  }
});

on("message-input", "keydown", (event) => {
  const menu = $("slash-menu");
  if (!menu) return;
  const open = !menu.hidden;
  const items = [...menu.querySelectorAll(".slash-item")];
  if (open && items.length) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      state.slashIndex = (state.slashIndex + 1) % items.length;
      showSlashMenu(slashQuery($("message-input").value) || "");
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      state.slashIndex = (state.slashIndex - 1 + items.length) % items.length;
      showSlashMenu(slashQuery($("message-input").value) || "");
      return;
    }
    if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey)) {
      event.preventDefault();
      applySlash(items[state.slashIndex].dataset.name);
      return;
    }
    if (event.key === "Escape") {
      hideSlashMenu();
      return;
    }
  }
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("composer")?.requestSubmit();
  }
});

window.addEventListener("resize", () => {
  if (!isMobile()) {
    $("app-shell").classList.remove("show-chat");
    $("mobile-back").hidden = true;
  } else if (state.chatId) showChatView(true);
});

(async function init() {
  const saved = storedTheme();
  applyTheme(saved || currentTheme(), { persist: false });
  setComposerEnabled(false);
  showChatView(false);
  updateTokenMeter();
  try {
    await loadStatus();
    await loadSkills();
    await loadChats();
  } catch (err) {
    setConnectStatus(String(err.message || err), "err");
  }
})();
