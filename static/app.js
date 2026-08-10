const SESSION_KEY = "portage.sessionTokens";
const EFFORT_KEY = "portage.effort";
const MODE_KEY = "portage.mode";
const THINKING_KEY = "portage.thinkingMode";

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
  streamAbort: null,
  streaming: false,
  streamChatId: null,
  streamPhase: null,
  workspaceFiles: [],
  mentionIndex: 0,
  lastFileChanges: [],
  effort: loadEffort(),
  mode: loadMode(),
  thinkingMode: loadThinkingMode(),
  supportsExtendedThinking: true,
};

function loadEffort() {
  try {
    const v = localStorage.getItem(EFFORT_KEY);
    const allowed = ["none", "low", "medium", "high", "extra_high", "max", "ultracode"];
    return allowed.includes(v) ? v : "high";
  } catch {
    return "high";
  }
}

function saveEffort(value) {
  state.effort = value;
  try {
    localStorage.setItem(EFFORT_KEY, value);
  } catch {
    /* ignore */
  }
}

function loadMode() {
  try {
    const v = localStorage.getItem(MODE_KEY);
    return v === "plan" ? "plan" : "agent";
  } catch {
    return "agent";
  }
}

function saveMode(value) {
  state.mode = value === "plan" ? "plan" : "agent";
  try {
    localStorage.setItem(MODE_KEY, state.mode);
  } catch {
    /* ignore */
  }
}

function loadThinkingMode() {
  try {
    const v = localStorage.getItem(THINKING_KEY);
    const allowed = ["adaptive", "extended", "off"];
    return allowed.includes(v) ? v : "adaptive";
  } catch {
    return "adaptive";
  }
}

function saveThinkingMode(value) {
  const allowed = ["adaptive", "extended", "off"];
  state.thinkingMode = allowed.includes(value) ? value : "adaptive";
  try {
    localStorage.setItem(THINKING_KEY, state.thinkingMode);
  } catch {
    /* ignore */
  }
}

function syncEffortSelect() {
  const el = $("effort-select");
  if (!el) return;
  el.value = state.effort || "high";
}

function syncModeSelect() {
  const el = $("mode-select");
  if (!el) return;
  el.value = state.mode || "agent";
}

function syncThinkingSelect() {
  const el = $("thinking-select");
  const wrap = $("thinking-wrap");
  if (el) el.value = state.thinkingMode || "adaptive";
  if (wrap) wrap.hidden = !state.supportsExtendedThinking;
}

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

function fmtExact(n) {
  return new Intl.NumberFormat("en-US").format(Number(n) || 0);
}

function fmtCompact(n) {
  const v = Number(n) || 0;
  if (v >= 10000) return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(v);
  return fmtExact(v);
}

function usageTotal(usage) {
  if (!usage) return 0;
  const inp = Number(usage.input_tokens) || 0;
  const out = Number(usage.output_tokens) || 0;
  return Number(usage.total_tokens) || inp + out;
}

function formatUsageTip(usage) {
  if (!usage) return "No token data yet";
  const inp = Number(usage.input_tokens) || 0;
  const out = Number(usage.output_tokens) || 0;
  const total = usageTotal(usage);
  return `In ${fmtExact(inp)} · Out ${fmtExact(out)} · Total ${fmtExact(total)}`;
}

function formatTokens(usage, { zeroOk = false } = {}) {
  const total = usageTotal(usage);
  if (!total && !zeroOk) return "—";
  return fmtCompact(total);
}

function updateTokenMeter() {
  const chatEl = $("token-chat");
  const chatTip = $("token-chat-tip");
  const sessionChip = $("token-session");
  const sessionVal = $("token-session-value");
  const sessionTip = $("token-session-tip");
  if (chatEl) chatEl.textContent = formatTokens(state.usageChat);
  if (chatTip) chatTip.textContent = formatUsageTip(state.usageChat);
  if (sessionVal) sessionVal.textContent = fmtCompact(usageTotal(state.usageSession));
  if (sessionTip) sessionTip.textContent = formatUsageTip(state.usageSession);
  const meter = $("token-meter");
  if (meter) meter.hidden = !state.chatId;
  if (sessionChip) sessionChip.hidden = !state.chatId;
}

function chatKey(chat) {
  return `${chat.source || "local"}::${chat.id}::${chat.transcript_path || ""}`;
}

function activeChatKey() {
  return `${state.chatSource || "local"}::${state.chatId || ""}::${state.transcriptPath || ""}`;
}

function addSessionUsage(usage) {
  if (!usage) return;
  const input = Number(usage.input_tokens) || 0;
  const output = Number(usage.output_tokens) || 0;
  const total = usageTotal(usage);
  state.usageLast = {
    input_tokens: input,
    output_tokens: output,
    total_tokens: total,
  };
  state.usageSession.input_tokens = (Number(state.usageSession.input_tokens) || 0) + input;
  state.usageSession.output_tokens = (Number(state.usageSession.output_tokens) || 0) + output;
  state.usageSession.total_tokens = (Number(state.usageSession.total_tokens) || 0) + total;
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
      "Enter to send · / skills · @ files · Esc stops · Shift+Enter newline";
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

function pathFromToolInput(input) {
  if (!input || typeof input !== "object") return "";
  return String(input.path || input.file_path || input.target_file || "");
}

function createBlocksPanel(blocks, { checkpointId = null } = {}) {
  if (!blocks || !blocks.length) return null;
  const wrap = document.createElement("div");
  wrap.className = "blocks-panel";
  const fileChanges = [];
  for (const block of blocks) {
    if (!block || typeof block !== "object") continue;
    if (block.type === "thinking") {
      const details = document.createElement("details");
      details.className = "think-card";
      const summary = document.createElement("summary");
      summary.textContent = block.subagent_id ? "Subagent thinking" : "Thinking";
      details.appendChild(summary);
      const pre = document.createElement("pre");
      pre.textContent = String(block.text || "").slice(0, 12000);
      details.appendChild(pre);
      wrap.appendChild(details);
    } else if (block.type === "subagent_start" || block.type === "subagent_done") {
      const details = document.createElement("details");
      details.className = "subagent-card";
      if (block.is_error) details.classList.add("is-error");
      const summary = document.createElement("summary");
      const label = block.label || "subagent";
      summary.textContent =
        block.type === "subagent_start"
          ? `Subagent · ${label}`
          : block.is_error
            ? `Subagent failed · ${label}`
            : `Subagent · ${label}`;
      details.appendChild(summary);
      const pre = document.createElement("pre");
      pre.textContent = String(block.summary || block.prompt || "").slice(0, 8000);
      details.appendChild(pre);
      wrap.appendChild(details);
    } else if (block.type === "tool_use") {
      const row = document.createElement("div");
      row.className = "tool-card";
      const path = pathFromToolInput(block.input);
      row.innerHTML = `<span class="tool-name">${escapeHtml(block.name || "tool")}</span>`;
      if (path) {
        const p = document.createElement("span");
        p.className = "tool-path";
        p.textContent = path;
        row.appendChild(p);
      }
      wrap.appendChild(row);
    } else if (block.type === "tool_result") {
      const details = document.createElement("details");
      details.className = "tool-result-card";
      const summary = document.createElement("summary");
      summary.textContent = block.is_error ? "Tool error" : "Tool result";
      details.appendChild(summary);
      const pre = document.createElement("pre");
      pre.textContent = String(block.content || "").slice(0, 4000);
      details.appendChild(pre);
      wrap.appendChild(details);
    } else if (block.type === "file_change") {
      fileChanges.push(block);
    }
  }
  if (fileChanges.length) {
    wrap.appendChild(createFilesChangedStrip(fileChanges, { checkpointId }));
  }
  return wrap.childNodes.length ? wrap : null;
}

function createFilesChangedStrip(changes, { checkpointId = null } = {}) {
  const strip = document.createElement("div");
  strip.className = "files-changed";
  const label = document.createElement("button");
  label.type = "button";
  label.className = "files-changed-btn";
  label.textContent = `${changes.length} file${changes.length === 1 ? "" : "s"} changed`;
  label.addEventListener("click", () => openDiffDrawer(changes));
  strip.appendChild(label);
  const names = document.createElement("span");
  names.className = "files-changed-names";
  names.textContent = changes
    .map((c) => c.path)
    .filter(Boolean)
    .slice(0, 4)
    .join(", ");
  strip.appendChild(names);
  if (checkpointId) {
    const reject = document.createElement("button");
    reject.type = "button";
    reject.className = "ghost tiny reject-btn";
    reject.textContent = "Reject";
    reject.title = "Restore files from checkpoint";
    reject.addEventListener("click", () => {
      const paths = changes.map((c) => c.path).filter(Boolean);
      const preview = paths.slice(0, 8).join("\n") + (paths.length > 8 ? "\n…" : "");
      const ok = window.confirm(
        `Restore ${paths.length} file${paths.length === 1 ? "" : "s"} from checkpoint?\n\n${preview}`
      );
      if (!ok) return;
      restoreCheckpoint(checkpointId, strip);
    });
    strip.appendChild(reject);
  }
  return strip;
}

function renderDiffHtml(diffText) {
  const lines = String(diffText || "").split("\n");
  return lines
    .map((line) => {
      let cls = "diff-line";
      if (line.startsWith("+++") || line.startsWith("---")) cls += " diff-file";
      else if (line.startsWith("@@")) cls += " diff-hunk";
      else if (line.startsWith("+")) cls += " diff-add";
      else if (line.startsWith("-")) cls += " diff-del";
      return `<span class="${cls}">${escapeHtml(line) || " "}</span>`;
    })
    .join("\n");
}

function openDiffDrawer(changes) {
  const drawer = $("diff-drawer");
  const list = $("diff-file-list");
  const view = $("diff-view");
  const backdrop = $("diff-backdrop");
  if (!drawer || !list || !view) return;
  state.lastFileChanges = changes || [];
  list.innerHTML = "";
  view.innerHTML = "";
  (changes || []).forEach((c, i) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = `${c.op || "update"} · ${c.path || "file"}`;
    btn.addEventListener("click", () => {
      list.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      view.innerHTML = renderDiffHtml(c.diff || "(no diff available)");
    });
    if (i === 0) {
      btn.classList.add("active");
      view.innerHTML = renderDiffHtml(c.diff || "(no diff available)");
    }
    li.appendChild(btn);
    list.appendChild(li);
  });
  drawer.hidden = false;
  if (backdrop) backdrop.hidden = false;
}

function closeDiffDrawer() {
  const drawer = $("diff-drawer");
  const backdrop = $("diff-backdrop");
  if (drawer) drawer.hidden = true;
  if (backdrop) backdrop.hidden = true;
}

async function restoreCheckpoint(checkpointId, stripEl = null) {
  if (!state.chatId || !checkpointId) return;
  try {
    const result = await api("/api/checkpoints/restore", {
      method: "POST",
      body: JSON.stringify({ chat_id: state.chatId, checkpoint_id: checkpointId }),
    });
    const note = $("sync-note");
    if (note) {
      note.hidden = false;
      const n = (result.restored || []).length + (result.deleted || []).length;
      note.textContent = `Restored checkpoint (${n} path${n === 1 ? "" : "s"}).`;
    }
    if (stripEl) {
      stripEl.classList.add("restored");
      const btn = stripEl.querySelector(".files-changed-btn");
      if (btn) {
        btn.disabled = true;
        btn.textContent = "Changes rejected";
      }
      const reject = stripEl.querySelector(".reject-btn");
      if (reject) reject.remove();
      const names = stripEl.querySelector(".files-changed-names");
      if (names) names.textContent = "Files restored from checkpoint";
    }
    closeDiffDrawer();
    hideCheckpointMenu();
    await refreshCheckpointMenu();
  } catch (err) {
    setConnectStatus(String(err.message || err), "err");
  }
}

function hideCheckpointMenu() {
  const menu = $("checkpoint-menu");
  const btn = $("checkpoint-btn");
  if (menu) menu.hidden = true;
  if (btn) btn.setAttribute("aria-expanded", "false");
}

async function refreshCheckpointMenu() {
  const wrap = $("checkpoint-wrap");
  const menu = $("checkpoint-menu");
  if (!wrap || !menu) return;
  if (!state.chatId || state.chatSource !== "local") {
    wrap.hidden = true;
    hideCheckpointMenu();
    return;
  }
  try {
    const data = await api(`/api/chats/${encodeURIComponent(state.chatId)}/checkpoints`);
    const list = data.checkpoints || [];
    wrap.hidden = list.length === 0;
    if (!list.length) {
      hideCheckpointMenu();
      return;
    }
    menu.innerHTML = list
      .slice(0, 12)
      .map((cp) => {
        const when = cp.created_at ? new Date(cp.created_at).toLocaleString() : cp.id;
        const n = (cp.paths || []).length;
        return `<button type="button" class="checkpoint-item" data-id="${escapeHtml(cp.id)}">
          <strong>${escapeHtml(String(when))}</strong>
          <span>${n} file${n === 1 ? "" : "s"}</span>
        </button>`;
      })
      .join("");
    menu.querySelectorAll(".checkpoint-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.dataset.id;
        const ok = window.confirm("Restore this checkpoint? Current file contents for those paths will be overwritten.");
        if (!ok) return;
        restoreCheckpoint(id);
      });
    });
  } catch {
    wrap.hidden = true;
  }
}

async function toggleCheckpointMenu() {
  const menu = $("checkpoint-menu");
  const btn = $("checkpoint-btn");
  if (!menu || !btn) return;
  if (!menu.hidden) {
    hideCheckpointMenu();
    return;
  }
  await refreshCheckpointMenu();
  if ($("checkpoint-wrap")?.hidden) return;
  menu.hidden = false;
  btn.setAttribute("aria-expanded", "true");
}

function createBubble(role, text, usage = null, extras = {}) {
  const blocks = extras.blocks || null;
  const fileChanges = extras.file_changes || null;
  const checkpointId = extras.checkpoint_id || null;
  const { text: cleaned, tools } = extractTools(text);
  const hasRich = (blocks && blocks.length) || (fileChanges && fileChanges.length);
  if (!cleaned && tools.length === 0 && !hasRich && role !== "assistant") return null;
  const div = document.createElement("article");
  div.className = `bubble ${role === "error" ? "error" : role}`;
  if (!cleaned && (tools.length || hasRich)) div.classList.add("tools-only");
  const roleEl = document.createElement("span");
  roleEl.className = "role";
  roleEl.textContent = role;
  div.appendChild(roleEl);

  const panel = createBlocksPanel(blocks, { checkpointId });
  if (panel) div.appendChild(panel);
  else if (tools.length) div.appendChild(createToolSummary(tools));

  if (fileChanges && fileChanges.length && !(blocks || []).some((b) => b.type === "file_change")) {
    div.appendChild(createFilesChangedStrip(fileChanges, { checkpointId }));
  }

  if (cleaned) {
    const body = document.createElement("div");
    body.className = "bubble-body";
    body.innerHTML = formatMessageHtml(cleaned);
    renderMath(body);
    div.appendChild(body);
  }
  if (role === "assistant" && usage && usageTotal(usage) > 0) {
    div.appendChild(createUsageBadge(usage));
  }
  return div;
}

function createUsageBadge(usage) {
  const badge = document.createElement("span");
  badge.className = "bubble-usage";
  badge.tabIndex = 0;
  const total = usageTotal(usage);
  badge.appendChild(document.createTextNode(`${fmtCompact(total)} tokens`));
  const tip = document.createElement("span");
  tip.className = "token-tip";
  tip.textContent = formatUsageTip(usage);
  tip.setAttribute("aria-hidden", "true");
  badge.appendChild(tip);
  return badge;
}

function createStreamingBubble() {
  const div = document.createElement("article");
  div.className = "bubble assistant streaming";
  const head = document.createElement("div");
  head.className = "bubble-head";
  const roleEl = document.createElement("span");
  roleEl.className = "role";
  roleEl.textContent = "assistant";
  head.appendChild(roleEl);
  const phaseEl = document.createElement("span");
  phaseEl.className = "stream-phase";
  phaseEl.textContent = "Starting…";
  head.appendChild(phaseEl);
  div.appendChild(head);
  const timeline = document.createElement("div");
  timeline.className = "tool-timeline";
  div.appendChild(timeline);
  const filesStripHost = document.createElement("div");
  filesStripHost.className = "files-changed-host";
  div.appendChild(filesStripHost);
  const body = document.createElement("div");
  body.className = "bubble-body";
  body.innerHTML = "";
  div.appendChild(body);
  return { bubble: div, body, timeline, filesStripHost, phaseEl, fileChanges: [] };
}

function phaseLabel(phase, detail) {
  switch (phase) {
    case "thinking":
      return "Thinking…";
    case "writing":
      return "Writing…";
    case "tool":
      return detail ? `Using tools (${detail})…` : "Using tools…";
    case "model":
      return detail === "streaming" ? "Waiting for model…" : "Waiting for model…";
    case "truncated":
      return "Truncated (max tokens)";
    default:
      return detail ? String(detail) : "Working…";
  }
}

function setStreamPhase(bubble, phaseEl, phase, detail) {
  state.streamPhase = phase || null;
  if (phaseEl) {
    phaseEl.textContent = phaseLabel(phase, detail);
    phaseEl.dataset.phase = phase || "";
  }
  if (bubble) {
    bubble.dataset.phase = phase || "";
  }
  const stop = $("stop-btn");
  if (stop && state.streaming) {
    stop.setAttribute("aria-label", `Stop · ${phaseLabel(phase, detail)}`);
  }
}

function paintStreamingBody(body, text) {
  body.innerHTML = formatMessageHtml(text || "");
}

function appendToolTimeline(timeline, event) {
  if (!timeline) return;
  if (event.type === "tool_start") {
    const row = document.createElement("div");
    row.className = "tool-card live";
    row.dataset.toolId = event.id || "";
    const path = pathFromToolInput(event.input);
    row.innerHTML = `<span class="tool-name">${escapeHtml(event.name || "tool")}</span>`;
    if (path) {
      const p = document.createElement("span");
      p.className = "tool-path";
      p.textContent = path;
      row.appendChild(p);
    }
    const st = document.createElement("span");
    st.className = "tool-status";
    st.textContent = "running…";
    row.appendChild(st);
    timeline.appendChild(row);
  } else if (event.type === "tool_result") {
    const row =
      [...timeline.querySelectorAll(".tool-card")].find((el) => el.dataset.toolId === event.id) ||
      null;
    if (row) {
      const st = row.querySelector(".tool-status");
      if (st) st.textContent = event.is_error ? "error" : "done";
      row.classList.toggle("is-error", !!event.is_error);
    }
  }
}

function appendThinkingTimeline(timeline, text, { subagentId = null } = {}) {
  if (!timeline || !text) return;
  let details = [...timeline.querySelectorAll(".think-card.live")].find((el) =>
    subagentId ? el.dataset.subagentId === subagentId : !el.dataset.subagentId
  );
  if (!details) {
    details = document.createElement("details");
    details.className = "think-card live";
    details.open = true;
    if (subagentId) details.dataset.subagentId = subagentId;
    const summary = document.createElement("summary");
    summary.textContent = subagentId ? "Subagent thinking" : "Thinking";
    details.appendChild(summary);
    const pre = document.createElement("pre");
    details.appendChild(pre);
    timeline.appendChild(details);
  }
  const pre = details.querySelector("pre");
  if (pre) {
    const next = (pre.textContent || "") + text;
    pre.textContent = next.length > 16000 ? next.slice(-16000) : next;
  }
}

function appendSubagentTimeline(timeline, event) {
  if (!timeline) return;
  if (event.type === "subagent_start") {
    const details = document.createElement("details");
    details.className = "subagent-card live";
    details.open = true;
    details.dataset.subagentId = event.id || "";
    const summary = document.createElement("summary");
    summary.textContent = `Subagent · ${event.label || "working"}…`;
    details.appendChild(summary);
    const pre = document.createElement("pre");
    pre.textContent = String(event.prompt || "");
    details.appendChild(pre);
    timeline.appendChild(details);
  } else if (event.type === "subagent_delta") {
    const details =
      [...timeline.querySelectorAll(".subagent-card")].find(
        (el) => el.dataset.subagentId === event.id
      ) || null;
    if (!details) return;
    const pre = details.querySelector("pre");
    if (pre) {
      const base = pre.dataset.streamed === "1" ? pre.textContent || "" : "";
      pre.dataset.streamed = "1";
      const next = base + (event.text || "");
      pre.textContent = next.length > 12000 ? next.slice(-12000) : next;
    }
  } else if (event.type === "subagent_done") {
    const details =
      [...timeline.querySelectorAll(".subagent-card")].find(
        (el) => el.dataset.subagentId === event.id
      ) || null;
    if (!details) return;
    details.classList.remove("live");
    details.classList.toggle("is-error", !!event.is_error);
    const summary = details.querySelector("summary");
    if (summary) {
      summary.textContent = event.is_error
        ? `Subagent failed · ${event.label || ""}`
        : `Subagent · ${event.label || "done"}`;
    }
    const pre = details.querySelector("pre");
    if (pre && event.summary) pre.textContent = String(event.summary).slice(0, 8000);
  }
}

function updateStreamingFiles(host, fileChanges) {
  if (!host) return;
  host.innerHTML = "";
  if (fileChanges.length) host.appendChild(createFilesChangedStrip(fileChanges));
}

function finalizeStreamingBubble(bubble, body, text, usage, extras = {}) {
  bubble.classList.remove("streaming");
  delete bubble.dataset.phase;
  const phase = bubble.querySelector(".stream-phase");
  if (phase) phase.remove();
  bubble.querySelectorAll(".tool-summary").forEach((el) => el.remove());
  const blocks = extras.blocks || null;
  const fileChanges = extras.file_changes || [];
  const checkpointId = extras.checkpoint_id || null;

  const panel = createBlocksPanel(blocks, { checkpointId });
  const timeline = bubble.querySelector(".tool-timeline");
  if (panel) {
    if (timeline) timeline.replaceWith(panel);
    else bubble.insertBefore(panel, body);
  } else {
    const { text: cleaned, tools } = extractTools(text);
    if (tools.length) {
      const summary = createToolSummary(tools);
      if (timeline) timeline.replaceWith(summary);
      else bubble.insertBefore(summary, body);
    } else if (timeline && !timeline.childNodes.length) timeline.remove();
  }

  const host = bubble.querySelector(".files-changed-host");
  if (fileChanges.length) {
    const strip = createFilesChangedStrip(fileChanges, { checkpointId });
    if (host) {
      host.innerHTML = "";
      host.appendChild(strip);
    } else bubble.insertBefore(strip, body);
  } else if (host) host.remove();

  const cleanedText = extractTools(text).text;
  if (cleanedText) {
    body.innerHTML = formatMessageHtml(cleanedText);
    renderMath(body);
  } else if (body) {
    body.remove();
  }
  bubble.querySelectorAll(".bubble-usage").forEach((el) => el.remove());
  if (usage && usageTotal(usage) > 0) {
    bubble.appendChild(createUsageBadge(usage));
  }
}

function setStreamingUi(on) {
  state.streaming = on;
  if (!on) state.streamPhase = null;
  const send = $("send-btn");
  const stop = $("stop-btn");
  if (send) {
    send.hidden = !!on;
    send.setAttribute("aria-hidden", on ? "true" : "false");
  }
  if (stop) {
    stop.hidden = !on;
    stop.setAttribute("aria-hidden", on ? "false" : "true");
    if (on) {
      stop.setAttribute("aria-label", "Stop generating");
      stop.focus();
    }
  }
  if (!on) setComposerEnabled(!!state.chatId);
}

function messagesNearBottom(root, threshold = 96) {
  if (!root) return true;
  return root.scrollHeight - root.scrollTop - root.clientHeight <= threshold;
}

function scrollMessages(root, { force = false } = {}) {
  if (!root) return;
  if (force || messagesNearBottom(root)) root.scrollTop = root.scrollHeight;
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

  const modelName = String(s.model || "");
  state.supportsExtendedThinking =
    s.supports_extended_thinking !== undefined
      ? !!s.supports_extended_thinking
      : s.provider === "foundry" || /claude/i.test(modelName);
  syncThinkingSelect();

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
  if (state.streaming) stopStreaming();
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
    loadWorkspaceFiles($("workspace-select").value || thread.workspace || null).catch(() => {});
    refreshCheckpointMenu().catch(() => {});
    const note = $("sync-note");
    if (note) {
      note.hidden = false;
      if (state.chatSource === "cursor" && $("writeback-toggle").checked) {
        note.textContent = "Continuing a Cursor chat — replies can write back to the transcript.";
      } else if (state.chatSource === "claude-code") {
        note.textContent = "Claude Code session — first reply continues as a local copy.";
      } else if (state.chatSource === "chatgpt" || state.chatSource === "antigravity") {
        note.textContent = `Imported from ${sourceLabel(state.chatSource)} — continue here on Foundry or Bedrock.`;
      } else if (thread.workspace) {
        note.textContent = "Workspace linked — agent can read/edit files. Use @ to mention a path.";
      } else {
        note.textContent = "Type / for skills · @ for files (link a workspace first).";
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
    root.innerHTML = `<p class="empty">Start typing — use / for skills or @ for files.</p>`;
    return;
  }
  for (const m of messages) {
    const hasBlocks = m.blocks && m.blocks.length;
    const hasChanges = m.file_changes && m.file_changes.length;
    if ((!m.text || !String(m.text).trim()) && !hasBlocks && !hasChanges) continue;
    const bubble = createBubble(m.role, m.text || "", m.usage || null, {
      blocks: m.blocks || null,
      file_changes: m.file_changes || null,
      checkpoint_id: m.checkpoint_id || null,
    });
    if (bubble) root.appendChild(bubble);
  }
  root.scrollTop = root.scrollHeight;
}

function stopStreaming() {
  if (state.streamAbort) {
    state.streamAbort.abort();
    state.streamAbort = null;
  }
}

async function sendMessage(event) {
  event.preventDefault();
  if (!state.chatId || state.streaming) return;
  const input = $("message-input");
  const text = input.value.trim();
  if (!text) return;
  hideSlashMenu();
  hideMentionMenu();

  const root = $("messages");
  if (root.querySelector(".empty") || root.querySelector(".empty-stage")) root.innerHTML = "";
  const userBubble = createBubble("user", text);
  if (userBubble) root.appendChild(userBubble);
  input.value = "";
  scrollMessages(root, { force: true });

  const streamUi = createStreamingBubble();
  const { bubble: replyBubble, body: replyBody, timeline, filesStripHost, phaseEl } = streamUi;
  const liveFileChanges = streamUi.fileChanges;
  root.appendChild(replyBubble);
  setStreamPhase(replyBubble, phaseEl, "model", "starting");
  scrollMessages(root, { force: true });

  const controller = new AbortController();
  state.streamAbort = controller;
  const streamChatId = state.chatId;
  state.streamChatId = streamChatId;
  setStreamingUi(true);
  input.disabled = true;
  const messagesEl = $("messages");
  if (messagesEl) messagesEl.setAttribute("aria-busy", "true");

  let assembled = "";
  let paintTimer = null;
  const schedulePaint = () => {
    if (paintTimer) return;
    paintTimer = window.setTimeout(() => {
      paintTimer = null;
      paintStreamingBody(replyBody, assembled);
      scrollMessages(root);
    }, 48);
  };

  let aborted = false;
  try {
    const resp = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: state.chatId,
        message: text,
        writeback: $("writeback-toggle").checked,
        source: state.chatSource,
        workspace: $("workspace-select").value || null,
        transcript_path: state.transcriptPath,
        effort: state.effort || $("effort-select")?.value || "high",
        mode: state.mode || $("mode-select")?.value || "agent",
        thinking_mode: state.supportsExtendedThinking
          ? state.thinkingMode || $("thinking-select")?.value || "adaptive"
          : "adaptive",
      }),
      signal: controller.signal,
    });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const err = await resp.json();
        detail = err.detail || detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let donePayload = null;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const line = chunk
          .split("\n")
          .map((l) => l.trim())
          .find((l) => l.startsWith("data:"));
        if (!line) continue;
        let event;
        try {
          event = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        if (event.type === "meta") {
          if (event.forked && event.chat_id) {
            state.chatId = event.chat_id;
            state.chatSource = event.source || "local";
            state.streamChatId = event.chat_id;
            state.transcriptPath = null;
            $("active-project").textContent = sourceLabel(state.chatSource);
          }
          if (event.agent === false) {
            const note = $("sync-note");
            if (note && !$("workspace-select")?.value) {
              note.hidden = false;
              note.textContent = "Link a workspace in Settings to enable file tools & diffs.";
            }
          }
        } else if (event.type === "status") {
          setStreamPhase(replyBubble, phaseEl, event.phase, event.detail);
        } else if (event.type === "delta" && event.text) {
          assembled += event.text;
          setStreamPhase(replyBubble, phaseEl, "writing");
          schedulePaint();
        } else if (event.type === "thinking" && event.text) {
          setStreamPhase(replyBubble, phaseEl, "thinking");
          appendThinkingTimeline(timeline, event.text, { subagentId: event.subagent_id || null });
          scrollMessages(root);
        } else if (event.type === "tool_start" || event.type === "tool_result") {
          setStreamPhase(replyBubble, phaseEl, "tool", event.name || "");
          appendToolTimeline(timeline, event);
          scrollMessages(root);
        } else if (
          event.type === "subagent_start" ||
          event.type === "subagent_delta" ||
          event.type === "subagent_done"
        ) {
          setStreamPhase(replyBubble, phaseEl, "tool", event.label || "subagent");
          appendSubagentTimeline(timeline, event);
          scrollMessages(root);
        } else if (event.type === "file_change") {
          liveFileChanges.push({
            path: event.path,
            op: event.op,
            diff: event.diff || "",
          });
          updateStreamingFiles(filesStripHost, liveFileChanges);
          scrollMessages(root);
        } else if (event.type === "done") {
          donePayload = event;
        } else if (event.type === "error") {
          throw new Error(event.detail || "Stream failed");
        }
      }
    }

    if (paintTimer) {
      clearTimeout(paintTimer);
      paintTimer = null;
    }

    const stillHere = state.chatId === state.streamChatId || state.chatId === streamChatId;
    if (!stillHere) {
      /* navigated away */
    } else if (donePayload) {
      const finalText = donePayload.reply || assembled;
      finalizeStreamingBubble(replyBubble, replyBody, finalText, donePayload.usage || null, {
        blocks: donePayload.blocks || null,
        file_changes: donePayload.file_changes || liveFileChanges,
        checkpoint_id: donePayload.checkpoint_id || null,
      });
      if (donePayload.usage) {
        addSessionUsage(donePayload.usage);
        state.usageChat = donePayload.usage_total || state.usageChat;
        updateTokenMeter();
      }
      if (donePayload.skill) {
        $("sync-note").hidden = false;
        $("sync-note").textContent =
          `Used skill /${donePayload.skill}` + (donePayload.writeback?.enabled ? " · wrote back to Cursor" : "");
      } else if (donePayload.writeback?.enabled) {
        $("sync-note").hidden = false;
        $("sync-note").textContent = donePayload.writeback.note || "Wrote back into Cursor transcript.";
      } else if (donePayload.forked) {
        $("sync-note").hidden = false;
        $("sync-note").textContent = "Continued as a local conversation (original transcript unchanged).";
      }
      scrollMessages(root);
      await loadChats();
      refreshCheckpointMenu().catch(() => {});
    } else {
      const partial = assembled.trim();
      if (partial || liveFileChanges.length) {
        finalizeStreamingBubble(replyBubble, replyBody, partial, null, {
          file_changes: liveFileChanges,
        });
      } else {
        replyBubble.remove();
      }
      const note = $("sync-note");
      if (note) {
        note.hidden = false;
        note.textContent = "Connection ended before the reply finished.";
      }
      try {
        await openChat(state.chatId, state.chatSource, state.transcriptPath);
      } catch {
        /* keep local paint */
      }
    }
  } catch (err) {
    if (paintTimer) {
      clearTimeout(paintTimer);
      paintTimer = null;
    }
    if (err.name === "AbortError") {
      aborted = true;
      const stillHere = state.chatId === state.streamChatId || state.chatId === streamChatId;
      const partial = assembled.trim();
      if (stillHere) {
        if (partial || liveFileChanges.length) {
          finalizeStreamingBubble(replyBubble, replyBody, partial, null, {
            file_changes: liveFileChanges,
          });
        } else {
          replyBubble.remove();
        }
        const note = $("sync-note");
        if (note) {
          note.hidden = false;
          note.textContent = "Generation stopped.";
        }
      }
    } else {
      replyBubble.remove();
      const fail = createBubble("error", String(err.message || err));
      if (fail) root.appendChild(fail);
    }
  } finally {
    state.streamAbort = null;
    state.streamChatId = null;
    setStreamingUi(false);
    if (messagesEl) messagesEl.removeAttribute("aria-busy");
    if (!aborted || state.chatId === streamChatId) input.focus();
  }
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

async function loadWorkspaceFiles(workspacePath) {
  if (!workspacePath) {
    state.workspaceFiles = [];
    return;
  }
  try {
    const data = await api(`/api/workspace/files?path=${encodeURIComponent(workspacePath)}`);
    state.workspaceFiles = data.files || [];
  } catch {
    state.workspaceFiles = [];
  }
}

function mentionQuery(value) {
  const m = String(value || "").match(/(^|\s)@([^\s@]*)$/);
  if (!m) return null;
  return m[2] || "";
}

function hideMentionMenu() {
  const menu = $("mention-menu");
  if (!menu) return;
  menu.hidden = true;
  menu.innerHTML = "";
}

function showMentionMenu(query) {
  const menu = $("mention-menu");
  if (!menu) return;
  const ws = $("workspace-select")?.value;
  if (!ws) {
    menu.hidden = false;
    menu.innerHTML = `<div class="slash-empty">Link a workspace in Settings to @ mention files.</div>`;
    return;
  }
  if (!(state.workspaceFiles || []).length) {
    menu.hidden = false;
    menu.innerHTML = `<div class="slash-empty">No indexed files yet — pick a workspace or wait for scan.</div>`;
    return;
  }
  const q = String(query || "").toLowerCase();
  const items = (state.workspaceFiles || [])
    .filter((f) => !q || f.toLowerCase().includes(q))
    .slice(0, 12);
  if (!items.length) {
    menu.hidden = false;
    menu.innerHTML = `<div class="slash-empty">No files match “${escapeHtml(q)}”.</div>`;
    return;
  }
  state.mentionIndex = Math.min(state.mentionIndex, items.length - 1);
  menu.hidden = false;
  menu.innerHTML = items
    .map(
      (f, i) => `
      <button type="button" class="slash-item${i === state.mentionIndex ? " active" : ""}" data-path="${escapeHtml(f)}">
        <strong>@${escapeHtml(f)}</strong>
      </button>`
    )
    .join("");
  menu.querySelectorAll(".slash-item").forEach((btn) => {
    btn.addEventListener("click", () => applyMention(btn.dataset.path));
  });
}

function applyMention(path) {
  const input = $("message-input");
  input.value = String(input.value || "").replace(/(^|\s)@[^\s@]*$/, `$1@${path} `);
  hideMentionMenu();
  input.focus();
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
on("stop-btn", "click", stopStreaming);
on("token-session", "click", (event) => {
  const chip = event.currentTarget;
  if (!chip || chip.hidden) return;
  chip.classList.toggle("tip-open");
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (state.streaming) {
    event.preventDefault();
    stopStreaming();
    return;
  }
  closeDiffDrawer();
  const menu = $("slash-menu");
  if (menu && !menu.hidden) hideSlashMenu();
  const mentions = $("mention-menu");
  if (mentions && !mentions.hidden) hideMentionMenu();
});
on("effort-select", "change", () => {
  const el = $("effort-select");
  if (el) saveEffort(el.value);
});
on("thinking-select", "change", () => {
  const el = $("thinking-select");
  if (el) saveThinkingMode(el.value);
});
on("mode-select", "change", () => {
  const el = $("mode-select");
  if (el) saveMode(el.value);
});
on("diff-close", "click", closeDiffDrawer);
on("diff-backdrop", "click", closeDiffDrawer);
on("checkpoint-btn", "click", () => toggleCheckpointMenu().catch(() => {}));
document.addEventListener("click", (event) => {
  const wrap = $("checkpoint-wrap");
  if (!wrap || wrap.hidden) return;
  if (!wrap.contains(event.target)) hideCheckpointMenu();
});
on("workspace-select", "change", () => {
  loadWorkspaceFiles($("workspace-select").value || null).catch(() => {});
});
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
  const value = $("message-input").value;
  const q = slashQuery(value);
  const mq = mentionQuery(value);
  if (q !== null) {
    hideMentionMenu();
    state.slashIndex = 0;
    showSlashMenu(q);
  } else if (mq !== null) {
    hideSlashMenu();
    state.mentionIndex = 0;
    showMentionMenu(mq);
  } else {
    hideSlashMenu();
    hideMentionMenu();
  }
});

on("message-input", "keydown", (event) => {
  const mentionMenu = $("mention-menu");
  const mentionOpen = mentionMenu && !mentionMenu.hidden;
  if (mentionOpen) {
    const items = [...mentionMenu.querySelectorAll(".slash-item")];
    if (items.length) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        state.mentionIndex = (state.mentionIndex + 1) % items.length;
        showMentionMenu(mentionQuery($("message-input").value) || "");
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        state.mentionIndex = (state.mentionIndex - 1 + items.length) % items.length;
        showMentionMenu(mentionQuery($("message-input").value) || "");
        return;
      }
      if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey)) {
        event.preventDefault();
        applyMention(items[state.mentionIndex].dataset.path);
        return;
      }
      if (event.key === "Escape") {
        hideMentionMenu();
        return;
      }
    }
  }
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
  syncEffortSelect();
  syncModeSelect();
  syncThinkingSelect();
  setStreamingUi(false);
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
