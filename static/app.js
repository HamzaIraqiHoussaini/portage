const state = {
  chatId: null,
  chats: [],
};

const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || data.message || res.statusText);
  }
  return data;
}

function setConnectStatus(text, kind = "") {
  const el = $("connect-status");
  el.textContent = text;
  el.className = `status ${kind}`.trim();
}

async function loadStatus() {
  const s = await api("/api/status");
  if (s.messages_url && !s.messages_url.includes("YOUR-RESOURCE")) {
    $("messages-url").value = s.messages_url;
  }
  if (s.model) $("model").value = s.model;
  $("writeback-toggle").checked = !!s.writeback_enabled;
  $("skills-meta").textContent = `Skills imported: ${s.skills_count} (${(s.priority_skills || []).slice(0, 6).join(", ")}${(s.priority_skills || []).length > 6 ? "…" : ""})`;
  $("settings-meta").textContent = `Cursor settings: ${s.settings_path}`;
  if (s.connected) {
    setConnectStatus(`Connected · ${s.model}`, "ok");
  } else if (s.has_key) {
    setConnectStatus("Key saved — test connection", "");
  } else {
    setConnectStatus("Not connected", "");
  }
}

async function connect() {
  const btn = $("connect-btn");
  btn.disabled = true;
  setConnectStatus("Testing Foundry…");
  try {
    const result = await api("/api/connect", {
      method: "POST",
      body: JSON.stringify({
        foundry_messages_url: $("messages-url").value.trim(),
        foundry_api_key: $("api-key").value.trim(),
        foundry_model: $("model").value.trim() || "claude-opus-5",
        anthropic_version: "2023-06-01",
      }),
    });
    setConnectStatus(`Connected · reply: ${result.test.reply}`, "ok");
    $("api-key").value = "";
    await loadStatus();
  } catch (err) {
    setConnectStatus(String(err.message || err), "err");
  } finally {
    btn.disabled = false;
  }
}

function renderChatList() {
  const root = $("chat-list");
  root.innerHTML = "";
  if (!state.chats.length) {
    root.innerHTML = `<p class="empty">No Cursor agent transcripts found under ~/.cursor/projects.</p>`;
    return;
  }
  for (const chat of state.chats) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `chat-item${chat.id === state.chatId ? " active" : ""}`;
    btn.innerHTML = `
      <span class="t">${escapeHtml(chat.title || chat.id)}</span>
      <span class="p">${escapeHtml(chat.project)}</span>
      <span class="prev">${escapeHtml(chat.preview || "")}</span>
    `;
    btn.addEventListener("click", () => openChat(chat.id));
    root.appendChild(btn);
  }
}

async function loadChats() {
  const data = await api("/api/chats");
  state.chats = data.chats || [];
  renderChatList();
}

async function openChat(id) {
  state.chatId = id;
  renderChatList();
  const thread = await api(`/api/chats/${encodeURIComponent(id)}`);
  $("active-title").textContent = thread.title || id;
  $("active-project").textContent = thread.project || "Cursor project";
  $("message-input").disabled = false;
  $("send-btn").disabled = false;
  renderMessages(thread.messages || []);
}

function renderMessages(messages) {
  const root = $("messages");
  root.innerHTML = "";
  if (!messages.length) {
    root.innerHTML = `<p class="empty">This transcript is empty.</p>`;
    return;
  }
  for (const m of messages) {
    if (!m.text || !String(m.text).trim()) continue;
    const div = document.createElement("div");
    div.className = `bubble ${m.role}`;
    div.innerHTML = `<span class="role">${escapeHtml(m.role)}</span>${escapeHtml(m.text)}`;
    root.appendChild(div);
  }
  root.scrollTop = root.scrollHeight;
}

async function sendMessage(event) {
  event.preventDefault();
  if (!state.chatId) return;
  const input = $("message-input");
  const text = input.value.trim();
  if (!text) return;

  $("send-btn").disabled = true;
  input.disabled = true;
  const root = $("messages");
  const pendingUser = document.createElement("div");
  pendingUser.className = "bubble user";
  pendingUser.innerHTML = `<span class="role">user</span>${escapeHtml(text)}`;
  root.appendChild(pendingUser);
  input.value = "";
  root.scrollTop = root.scrollHeight;

  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        chat_id: state.chatId,
        message: text,
        writeback: $("writeback-toggle").checked,
      }),
    });
    const reply = document.createElement("div");
    reply.className = "bubble assistant";
    reply.innerHTML = `<span class="role">assistant</span>${escapeHtml(result.reply || "")}`;
    root.appendChild(reply);
    if (result.writeback?.enabled) {
      $("sync-note").textContent = result.writeback.note || "Wrote back into Cursor transcript.";
    }
    root.scrollTop = root.scrollHeight;
  } catch (err) {
    const fail = document.createElement("div");
    fail.className = "bubble assistant";
    fail.innerHTML = `<span class="role">error</span>${escapeHtml(String(err.message || err))}`;
    root.appendChild(fail);
  } finally {
    input.disabled = false;
    $("send-btn").disabled = false;
    input.focus();
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

$("connect-btn").addEventListener("click", connect);
$("refresh-chats").addEventListener("click", () => loadChats().catch(console.error));
$("composer").addEventListener("submit", sendMessage);

(async function init() {
  try {
    await loadStatus();
    await loadChats();
  } catch (err) {
    setConnectStatus(String(err.message || err), "err");
  }
})();
