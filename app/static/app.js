const state = {
  mode: "chat",
  chapterId: null,
  runId: null,
  eventSource: null,
  manuscriptFiles: [],
};

const el = (id) => document.getElementById(id);

function appendLog(targetId, text) {
  const target = el(targetId);
  if (!target) return;
  const current = target.textContent || "";
  target.textContent = `${current}${current ? "\n" : ""}${text}`;
  target.scrollTop = target.scrollHeight;
}

function setChapterSelection(chapter) {
  state.chapterId = chapter ? chapter.id : null;
  if (chapter) {
    el("chapter-title").value = chapter.title || "";
    el("chapter-path").value = chapter.canonical_path || "";
    el("chapter-selected").textContent = `Selected chapter ${chapter.id}: ${chapter.title} (${chapter.canonical_path})`;
  } else {
    el("chapter-selected").textContent = "No chapter selected yet.";
  }
}

function setMode(mode) {
  state.mode = mode;
  el("mode-chat").classList.toggle("active", mode === "chat");
  el("mode-agent").classList.toggle("active", mode === "agent");
  el("chat-panel").style.opacity = mode === "chat" ? "1" : "0.92";
  el("agent-panel").style.opacity = mode === "agent" ? "1" : "0.92";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${response.status}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

async function buildSpine() {
  const title = el("chapter-title").value.trim();
  const canonicalPath = el("chapter-path").value.trim();
  const beats = el("beats-input")
    .value.split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (!title || !canonicalPath || beats.length === 0) {
    appendLog("run-log", "Build Spine requires title, canonical path, and at least one beat.");
    return;
  }

  let chapter;
  if (state.chapterId) {
    chapter = await api(`/chapters/${state.chapterId}`, {
      method: "PATCH",
      body: JSON.stringify({ title, canonical_path: canonicalPath }),
    });
  } else {
    chapter = await api("/chapters", {
      method: "POST",
      body: JSON.stringify({ title, canonical_path: canonicalPath }),
    });
  }
  setChapterSelection(chapter);
  const savedBeats = await api(`/chapters/${chapter.id}/beats`, {
    method: "POST",
    body: JSON.stringify({ beats }),
  });
  renderBeats(savedBeats);
  appendLog("run-log", `Spine saved for chapter ${chapter.id} with ${savedBeats.length} beats.`);
  setMode("agent");
}

async function reviewSpine() {
  if (!state.chapterId) {
    appendLog("run-log", "No chapter loaded yet.");
    return;
  }
  const beats = await api(`/chapters/${state.chapterId}/beats`);
  renderBeats(beats);
  appendLog("run-log", "Spine loaded for review.");
}

async function lockSpine() {
  if (!state.chapterId) {
    appendLog("run-log", "No chapter loaded yet.");
    return;
  }
  const locked = await api(`/chapters/${state.chapterId}/lock-spine`, { method: "POST" });
  renderBeats(locked.beats);
  appendLog("run-log", `Spine locked (${locked.locked_count} beats).`);
}

function renderBeats(beats) {
  const list = el("beats-list");
  list.innerHTML = "";
  beats.forEach((beat) => {
    const item = document.createElement("li");
    item.textContent = `[${beat.status}] Beat ${beat.beat_order}: ${beat.summary}`;
    list.appendChild(item);
  });
}

async function startRun() {
  if (!state.chapterId) {
    appendLog("run-log", "Build and lock a chapter spine first.");
    return;
  }
  const mode = el("run-mode").value;
  const run = await api("/runs", {
    method: "POST",
    body: JSON.stringify({ chapter_id: state.chapterId, mode }),
  });
  state.runId = run.id;
  appendLog("run-log", `Run ${run.id} started in ${mode} mode.`);
  connectEventStream(run.id);
  await refreshRunStatus();
}

function connectEventStream(runId) {
  if (state.eventSource) {
    state.eventSource.close();
  }
  const source = new EventSource(`/runs/${runId}/events`);
  source.onmessage = async () => {
    await refreshRunStatus();
    await refreshPreview();
  };
  source.addEventListener("approval_required", (evt) => {
    appendLog("run-log", `Approval needed: ${evt.data}`);
  });
  source.addEventListener("fragment_revised", (evt) => {
    appendLog("run-log", `Revised fragment: ${evt.data}`);
  });
  source.addEventListener("run_completed", (evt) => {
    appendLog("run-log", `Run complete: ${evt.data}`);
  });
  source.onerror = () => {
    source.close();
  };
  state.eventSource = source;
}

async function refreshRunStatus() {
  if (!state.runId) return;
  const run = await api(`/runs/${state.runId}`);
  const todos = await api(`/runs/${state.runId}/todos`);
  el("run-status").textContent = `Run ${run.id} | status: ${run.status} | mode: ${run.mode}`;
  const checkpoint = run.checkpoint_json || {};
  el("run-checkpoint").textContent =
    `Checkpoint: ${checkpoint.completed_todos || 0}/${checkpoint.total_todos || todos.length} completed`;
}

async function refreshPreview() {
  if (!state.chapterId) return;
  const preview = await api(`/chapters/${state.chapterId}/preview`);
  el("preview-text").textContent = preview.preview || "(No approved fragments yet.)";
}

async function refreshManuscriptFiles() {
  const data = await api("/manuscript/files");
  state.manuscriptFiles = data.files || [];
  const select = el("existing-manuscript");
  select.innerHTML = "";
  if (state.manuscriptFiles.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No manuscript files found under manuscript/";
    select.appendChild(option);
    return;
  }
  state.manuscriptFiles.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.path;
    option.textContent = `${item.path}${item.linked ? " (linked)" : ""}`;
    select.appendChild(option);
  });
}

async function uploadManuscript() {
  const input = el("manuscript-file");
  const file = input.files && input.files[0];
  if (!file) {
    appendLog("run-log", "Choose a manuscript file first.");
    return;
  }
  const text = await file.text();
  const chapterTitle = el("chapter-title").value.trim();
  const payload = {
    filename: file.name,
    content: text,
    chapter_title: chapterTitle || null,
    overwrite: false,
  };
  const result = await api("/manuscript/upload", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  setChapterSelection(result.chapter);
  await refreshManuscriptFiles();
  appendLog("run-log", `Manuscript uploaded and ${result.linked} chapter linked: ${result.canonical_path}`);
}

async function linkManuscriptFile() {
  const select = el("existing-manuscript");
  const canonicalPath = (select.value || "").trim();
  if (!canonicalPath) {
    appendLog("run-log", "Select a manuscript file first.");
    return;
  }
  const chapterTitle = el("chapter-title").value.trim();
  const result = await api("/chapters/link-manuscript", {
    method: "POST",
    body: JSON.stringify({ canonical_path: canonicalPath, chapter_title: chapterTitle || null }),
  });
  setChapterSelection(result.chapter);
  appendLog("run-log", `Manuscript linked (${result.linked}): ${result.canonical_path}`);
}

function startNewChapter() {
  state.chapterId = null;
  state.runId = null;
  setChapterSelection(null);
  el("run-status").textContent = "No run active.";
  el("run-checkpoint").textContent = "";
  appendLog("run-log", "Ready to create a new chapter draft.");
}

async function publishChapter() {
  if (!state.chapterId) {
    appendLog("run-log", "Select or create a chapter before publishing.");
    return;
  }
  const result = await api(`/chapters/${state.chapterId}/publish`, {
    method: "POST",
    body: JSON.stringify({ overwrite: true }),
  });
  appendLog("run-log", `Published to ${result.canonical_path} (${result.bytes_written} bytes).`);
}

async function uploadSourceConfig() {
  const input = el("source-config-file");
  const file = input.files && input.files[0];
  if (!file) {
    appendLog("run-log", "Choose a JSON source config file first.");
    return;
  }
  const content = await file.text();
  const result = await api("/integrations/sources/upload", {
    method: "POST",
    body: JSON.stringify({
      filename: file.name,
      content,
    }),
  });
  appendLog("run-log", `Source config uploaded from ${result.filename}.`);
  await refreshSourceConfig();
}

async function refreshSourceConfig() {
  const data = await api("/integrations/sources");
  el("source-config-view").textContent = JSON.stringify(data.sources || {}, null, 2);
}

async function validateSourceConfig() {
  const data = await api("/integrations/sources");
  const entries = Object.entries(data.sources || {});
  const sources = entries
    .filter(([, config]) => config && Object.keys(config).length > 0)
    .map(([source]) => source);
  if (sources.length === 0) {
    appendLog("run-log", "No configured sources found to validate.");
    return;
  }

  const report = {};
  for (const source of sources) {
    const result = await api(`/integrations/sources/${source}/validate`);
    report[source] = result.validation;
  }
  el("source-config-view").textContent = JSON.stringify(report, null, 2);
  const failed = sources.filter((source) => !report[source].ok);
  if (failed.length > 0) {
    appendLog("run-log", `Source validation failed: ${failed.join(", ")}`);
    return;
  }
  appendLog("run-log", `Source validation passed for ${sources.length} source(s).`);
}

async function sendChat() {
  const message = el("chat-input").value.trim();
  if (!message) return;
  appendLog("chat-log", `You: ${message}`);
  el("chat-input").value = "";
  const reply = await api("/chat", {
    method: "POST",
    body: JSON.stringify({ message, chapter_id: state.chapterId }),
  });
  appendLog("chat-log", `Agent (${reply.provider}):\n${reply.reply}`);
}

async function runAction(action) {
  if (!state.runId) {
    appendLog("run-log", "No active run.");
    return;
  }
  const notes = el("revise-notes").value.trim();
  await api(`/runs/${state.runId}/actions`, {
    method: "POST",
    body: JSON.stringify({ action, notes: notes || null }),
  });
  appendLog("run-log", `Action sent: ${action}`);
  await refreshRunStatus();
  await refreshPreview();
}

function bind() {
  el("mode-chat").addEventListener("click", () => setMode("chat"));
  el("mode-agent").addEventListener("click", () => setMode("agent"));
  el("build-spine").addEventListener("click", () => buildSpine().catch((e) => appendLog("run-log", e.message)));
  el("review-spine").addEventListener("click", () => reviewSpine().catch((e) => appendLog("run-log", e.message)));
  el("lock-spine").addEventListener("click", () => lockSpine().catch((e) => appendLog("run-log", e.message)));
  el("start-run").addEventListener("click", () => startRun().catch((e) => appendLog("run-log", e.message)));
  el("send-chat").addEventListener("click", () => sendChat().catch((e) => appendLog("chat-log", e.message)));
  el("upload-manuscript").addEventListener("click", () => uploadManuscript().catch((e) => appendLog("run-log", e.message)));
  el("refresh-manuscripts").addEventListener("click", () => refreshManuscriptFiles().catch((e) => appendLog("run-log", e.message)));
  el("link-manuscript").addEventListener("click", () => linkManuscriptFile().catch((e) => appendLog("run-log", e.message)));
  el("new-chapter").addEventListener("click", () => startNewChapter());
  el("publish-btn").addEventListener("click", () => publishChapter().catch((e) => appendLog("run-log", e.message)));
  el("upload-source-config").addEventListener("click", () => uploadSourceConfig().catch((e) => appendLog("run-log", e.message)));
  el("refresh-source-config").addEventListener("click", () => refreshSourceConfig().catch((e) => appendLog("run-log", e.message)));
  el("validate-source-config").addEventListener("click", () => validateSourceConfig().catch((e) => appendLog("run-log", e.message)));

  el("approve-btn").addEventListener("click", () => runAction("approve").catch((e) => appendLog("run-log", e.message)));
  el("revise-btn").addEventListener("click", () => runAction("revise").catch((e) => appendLog("run-log", e.message)));
  el("pause-btn").addEventListener("click", () => runAction("pause").catch((e) => appendLog("run-log", e.message)));
  el("resume-btn").addEventListener("click", () => runAction("resume").catch((e) => appendLog("run-log", e.message)));
  el("cancel-btn").addEventListener("click", () => runAction("cancel").catch((e) => appendLog("run-log", e.message)));
}

bind();
setMode("chat");
refreshManuscriptFiles().catch((e) => appendLog("run-log", e.message));
refreshSourceConfig().catch((e) => appendLog("run-log", e.message));
