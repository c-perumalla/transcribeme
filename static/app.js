"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  sessions: [], skills: [], current: null,
  recordingId: null, stream: null, timerHandle: null, startedAt: 0,
  provisional: [],
};

// --- helpers --------------------------------------------------------------

const clock = (s) => {
  s = Math.max(0, Math.floor(s || 0));
  const h = String(Math.floor(s / 3600)).padStart(2, "0");
  const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const sec = String(s % 60).padStart(2, "0");
  return h === "00" ? `${m}:${sec}` : `${h}:${m}:${sec}`;
};

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

// Minimal Markdown rendering — enough for the summary shapes the skills emit.
// Text is escaped first, so model output can never inject markup.
function renderMarkdown(md) {
  const esc = (t) => t.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const lines = esc(md).split("\n");
  const out = [];
  let inList = false, inTable = false;

  const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };
  const closeTable = () => { if (inTable) { out.push("</table>"); inTable = false; } };

  for (const line of lines) {
    const t = line.trim();
    const heading = t.match(/^(#{1,6})\s+(.*)$/);
    const bullet = t.match(/^[-*]\s+(.*)$/);
    const isTableRow = t.startsWith("|") && t.endsWith("|");

    if (heading) {
      closeList(); closeTable();
      const level = Math.min(6, heading[1].length + 2);
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
    } else if (isTableRow) {
      closeList();
      const cells = t.slice(1, -1).split("|").map((c) => c.trim());
      if (cells.every((c) => /^-{2,}$/.test(c.replace(/:/g, "")))) continue;
      if (!inTable) { out.push('<table class="md">'); inTable = true; }
      const tag = out[out.length - 1] === '<table class="md">' ? "th" : "td";
      out.push("<tr>" + cells.map((c) => `<${tag}>${inline(c)}</${tag}>`).join("") + "</tr>");
    } else if (bullet) {
      closeTable();
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${inline(bullet[1])}</li>`);
    } else if (!t) {
      closeList(); closeTable();
    } else {
      closeList(); closeTable();
      out.push(`<p>${inline(t)}</p>`);
    }
  }
  closeList(); closeTable();
  return out.join("");

  function inline(t) {
    return t
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`(.+?)`/g, "<code>$1</code>")
      .replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }
}

// --- bootstrap ------------------------------------------------------------

async function init() {
  bindTabs();
  await Promise.all([loadSkills(), loadDevices(), loadSessions()]);

  $("btn-record").onclick = startRecording;
  $("btn-stop").onclick = stopRecording;
  $("btn-upload").onclick = uploadFile;
  $("btn-close").onclick = () => { $("detail").hidden = true; state.current = null; renderSessions(); };
  $("btn-resummarize").onclick = resummarize;
  $("btn-delete").onclick = deleteSession;
  $("btn-rename").onclick = renameSpeakers;

  setInterval(loadSessions, 4000);
}

function bindTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.onclick = () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
      $("tab-record").hidden = tab.dataset.tab !== "record";
      $("tab-upload").hidden = tab.dataset.tab !== "upload";
    };
  });
}

async function loadSkills() {
  try {
    const { skills } = await api("/api/skills");
    state.skills = skills;
    for (const id of ["rec-skill", "up-skill", "detail-skill"]) {
      const sel = $(id);
      sel.innerHTML = skills.map((s) => `<option value="${s.id}">${s.name}</option>`).join("")
        || '<option value="">No skills found</option>';
    }
  } catch (e) { console.error(e); }
}

async function loadDevices() {
  const select = $("device");
  try {
    const { devices } = await api("/api/devices");
    select.innerHTML = devices
      .map((d) => `<option value="${d.name}"${d.is_aggregate ? " selected" : ""}>${d.name}</option>`)
      .join("");
    const aggregate = devices.find((d) => d.is_aggregate);
    $("device-hint").textContent = aggregate
      ? `Using "${aggregate.name}" — your mic and system audio will be separated into distinct speakers.`
      : "No Aggregate Device found. Single-device capture works, but system audio won't be captured. See the README to set one up.";
  } catch (e) {
    select.innerHTML = "<option>unavailable</option>";
    $("device-hint").textContent = e.message;
  }
}

async function loadSessions() {
  try {
    const { sessions } = await api("/api/sessions");
    state.sessions = sessions;
    renderSessions();
    // Refresh an open detail view whose job is still moving.
    if (state.current && ["processing", "recording"].includes(
      sessions.find((s) => s.id === state.current)?.status)) {
      await openSession(state.current, { keepStream: true });
    }
  } catch (e) { console.error(e); }
}

function renderSessions() {
  $("session-list").innerHTML = state.sessions.map((s) => `
    <li data-id="${s.id}" class="${s.id === state.current ? "active" : ""}">
      <span class="s-title"><span class="dot ${s.status}"></span>${escapeHtml(s.title)}</span>
      <span class="s-meta">${s.created_at.replace("T", " ").slice(0, 16)}
        · ${clock(s.duration_s)}
        ${s.speaker_count ? `· ${s.speaker_count} speakers` : ""}
        ${s.status === "processing" ? `· ${s.stage || ""}` : ""}
        ${s.status === "error" ? "· failed" : ""}</span>
    </li>`).join("") || '<li class="muted">Nothing yet.</li>';

  document.querySelectorAll("#session-list li[data-id]").forEach((li) => {
    li.onclick = () => openSession(li.dataset.id);
  });
}

const escapeHtml = (t) => String(t ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// --- recording ------------------------------------------------------------

async function startRecording() {
  $("btn-record").disabled = true;
  try {
    const body = new FormData();
    body.append("device", $("device").value);
    body.append("title", $("rec-title").value);
    body.append("skill", $("rec-skill").value);

    const { id } = await api("/api/recordings", { method: "POST", body });
    state.recordingId = id;
    state.provisional = [];
    state.startedAt = Date.now();

    $("btn-stop").hidden = false;
    $("timer").hidden = false;
    state.timerHandle = setInterval(() => {
      $("timer").textContent = clock((Date.now() - state.startedAt) / 1000);
    }, 500);

    await loadSessions();
    await openSession(id);
  } catch (e) {
    alert(`Could not start recording: ${e.message}`);
    $("btn-record").disabled = false;
  }
}

async function stopRecording() {
  const id = state.recordingId;
  if (!id) return;
  $("btn-stop").disabled = true;
  try {
    await api(`/api/recordings/${id}/stop`, { method: "POST" });
  } catch (e) {
    alert(`Stop failed: ${e.message}`);
  } finally {
    clearInterval(state.timerHandle);
    state.recordingId = null;
    $("btn-stop").hidden = true;
    $("btn-stop").disabled = false;
    $("btn-record").disabled = false;
    $("timer").hidden = true;
    await loadSessions();
  }
}

async function uploadFile() {
  const file = $("upload-file").files[0];
  if (!file) { alert("Pick an audio file first."); return; }

  $("btn-upload").disabled = true;
  try {
    const body = new FormData();
    body.append("file", file);
    body.append("title", $("up-title").value);
    body.append("skill", $("up-skill").value);
    if ($("up-speakers").value) body.append("num_speakers", $("up-speakers").value);

    const { id } = await api("/api/sessions", { method: "POST", body });
    await loadSessions();
    await openSession(id);
  } catch (e) {
    alert(`Upload failed: ${e.message}`);
  } finally {
    $("btn-upload").disabled = false;
  }
}

// --- detail view ----------------------------------------------------------

async function openSession(id, { keepStream = false } = {}) {
  const session = await api(`/api/sessions/${id}`);
  const switching = state.current !== id;
  state.current = id;
  if (switching) state.provisional = [];

  $("detail").hidden = false;
  $("detail-title").textContent = session.title;
  renderSessions();

  const active = ["processing", "recording"].includes(session.status);
  $("progress-wrap").hidden = !active;
  $("bar-fill").style.width = `${Math.round((session.progress || 0) * 100)}%`;
  $("stage-label").textContent = session.status === "recording"
    ? "recording…" : (session.stage || "");

  $("detail-error").hidden = !session.error;
  $("detail-error").textContent = session.error || "";

  $("drive-note").hidden = !session.drive_path;
  $("drive-note").textContent = session.drive_path
    ? `Saved to Drive: ${session.drive_path}` : "";

  if (session.skill_name) $("detail-skill").value = session.skill_name;
  for (const [elId, kind, fmt] of [
    ["dl-bundle", "bundle", "md"], ["dl-summary", "summary", "md"],
    ["dl-md", "transcript", "md"], ["dl-txt", "transcript", "txt"],
    ["dl-srt", "transcript", "srt"], ["dl-json", "transcript", "json"],
  ]) {
    $(elId).href = `/api/sessions/${id}/download?kind=${kind}&fmt=${fmt}`;
  }

  $("summary").innerHTML = session.summary_md
    ? renderMarkdown(session.summary_md)
    : '<span class="muted">No summary yet.</span>';

  renderTranscript(session.utterances);

  if (!keepStream && active) connectStream(id);
}

function renderTranscript(utterances) {
  const finals = (utterances || []).map((u) => ({ ...u, provisional: false }));
  const all = finals.length ? finals : state.provisional;

  $("transcript").innerHTML = all.map((u) => `
    <div class="utt ${u.provisional ? "provisional" : ""}">
      <div class="utt-head">
        <span class="utt-speaker">${escapeHtml(u.speaker)}</span>
        ${clock(u.start)}
        ${u.provisional ? '<span class="badge">live</span>' : ""}
      </div>
      <div>${escapeHtml(u.text)}</div>
    </div>`).join("")
    || '<p class="muted">No transcript yet.</p>';
}

function connectStream(id) {
  if (state.stream) state.stream.close();
  const source = new EventSource(`/api/sessions/${id}/stream`);
  state.stream = source;

  source.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "partial") {
      state.provisional.push({ ...data, provisional: true });
      state.provisional.sort((a, b) => a.start - b.start);
      renderTranscript(null);
    } else if (data.type === "stage") {
      $("progress-wrap").hidden = false;
      $("bar-fill").style.width = `${Math.round(data.progress * 100)}%`;
      $("stage-label").textContent = data.note ? `${data.stage} — ${data.note}` : data.stage;
    } else if (data.type === "transcript") {
      state.provisional = [];
      renderTranscript(data.utterances);
    } else if (data.type === "summary") {
      $("summary").innerHTML = renderMarkdown(data.markdown);
    } else if (data.type === "error") {
      $("detail-error").hidden = false;
      $("detail-error").textContent = data.message;
      source.close();
    } else if (data.type === "done") {
      source.close();
      loadSessions().then(() => openSession(id, { keepStream: true }));
    }
  };
  source.onerror = () => source.close();
}

async function resummarize() {
  if (!state.current) return;
  $("btn-resummarize").disabled = true;
  try {
    const body = new FormData();
    body.append("skill", $("detail-skill").value);
    await api(`/api/sessions/${state.current}/summarize`, { method: "POST", body });
    connectStream(state.current);
  } catch (e) {
    alert(`Re-summarize failed: ${e.message}`);
  } finally {
    $("btn-resummarize").disabled = false;
  }
}

async function deleteSession() {
  if (!state.current || !confirm("Delete this session and its audio?")) return;
  await api(`/api/sessions/${state.current}`, { method: "DELETE" });
  state.current = null;
  $("detail").hidden = true;
  await loadSessions();
}

async function renameSpeakers() {
  const session = await api(`/api/sessions/${state.current}`);
  const labels = [...new Set(session.utterances.map((u) => u.speaker_id || u.speaker))];
  if (!labels.length) return;

  const names = { ...(session.speaker_names || {}) };
  for (const label of labels) {
    const entered = prompt(`Display name for ${label}:`, names[label] || label);
    if (entered === null) return;          // cancel abandons the whole rename
    if (entered.trim()) names[label] = entered.trim();
  }

  const { utterances } = await api(`/api/sessions/${state.current}/speakers`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(names),
  });
  renderTranscript(utterances);
  await loadSessions();
}

init();
