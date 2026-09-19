"use strict";
const $ = (id) => document.getElementById(id);
let apiKey = "", selected = null, busy = false;
let voiceNote = null, uploadId = null, cloudVoice = false, questionRequest = null;
function notice(text) { $("notice").textContent = text; }
async function api(path, body) {
  const response = await fetch("/api" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Authorization": "Bearer " + apiKey, "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || data.detail || "Request failed.");
  return data;
}
function dirty() { return selected && $("draft-text").value !== selected.text; }
function buttons() {
  const status = selected?.status;
  const editable = ["pending", "approved", "delivery_failed"].includes(status);
  $("save-edit").disabled = busy || !editable || !dirty();
  $("regenerate").disabled = busy || !editable || dirty();
  $("approve").disabled = busy || status !== "pending" || dirty();
  $("reject").disabled = busy || !editable || dirty();
  $("deliver").disabled = busy || !["approved", "delivery_failed"].includes(status) || dirty();
  $("draft-text").disabled = busy;
  $("draft-text").readOnly = !editable;
  $("unsaved").textContent = dirty() ? "Unsaved edits. Save them before approving or delivering." : "";
}
async function run(action) {
  if (busy) return;
  busy = true; buttons(); notice("Working…");
  try { await action(); } catch (error) { notice(error.message); }
  finally { busy = false; buttons(); }
}
async function load() {
  const [status, personas, drafts, notes, questions] = await Promise.all([api("/status"), api("/personas"), api("/drafts"), api("/voice"), api("/assistant/requests")]);
  $("status").textContent = JSON.stringify(status, null, 2);
  $("personas").textContent = personas.map(p => `${p.display_name} (${p.recipient_id}) · version ${p.version} · ${p.style.formality}`).join("\n") || "No personas yet.";
  const previous = $("draft-recipient").value;
  $("draft-recipient").replaceChildren();
  personas.forEach(p => { const o = document.createElement("option"); o.value = p.recipient_id; o.textContent = p.display_name; $("draft-recipient").append(o); });
  if (personas.some(p => p.recipient_id === previous)) $("draft-recipient").value = previous;
  for (const id of ["voice-recipient", "question-recipient"]) {
    const old = $(id).value;
    $(id).replaceChildren();
    personas.forEach(p => { const o = document.createElement("option"); o.value = p.recipient_id; o.textContent = p.display_name; $(id).append(o); });
    if (personas.some(p => p.recipient_id === old)) $(id).value = old;
  }
  cloudVoice = status.voice.uploads_audio_to_provider;
  $("voice-processing").textContent = cloudVoice
    ? "ElevenLabs transcription: uploading a memo sends its original audio to ElevenLabs. The returned text is redacted before storage and draft generation."
    : "Local transcription: audio is processed on the backend machine. The model may download on first use. Recordings are not retained by this app.";
  $("voice-notes").replaceChildren();
  notes.forEach(note => {
    const b = document.createElement("button"); b.type = "button";
    b.textContent = `Memo · ${note.status} · ${note.created_at}`;
    b.onclick = () => run(async () => {
      if (dirty() && !confirm("Discard unsaved draft edits?")) return;
      if (note.draft_id && note.status === "draft_ready") await show(await api(`/drafts/${note.draft_id}`));
      else openVoice(await api(`/voice/${note.id}`));
      notice("Memo loaded.");
    });
    $("voice-notes").append(b);
  });
  $("question-results").replaceChildren();
  questions.forEach(item => {
    const article = document.createElement("article"), text = document.createElement("p");
    const detail = item.status === "resolved" ? item.resolution : item.answer;
    text.textContent = `${item.question} — ${item.status}${item.reason ? ` (${item.reason})` : ""}. ${detail || ""}`;
    article.append(text);
    if (item.draft_id) {
      const b = document.createElement("button"); b.textContent = "Review answer draft";
      b.onclick = () => run(async () => { if (dirty() && !confirm("Discard unsaved draft edits?")) return; await show(await api(`/drafts/${item.draft_id}`)); notice("Review the answer before approval."); });
      article.append(b);
    }
    if (item.status === "escalated") {
      const b = document.createElement("button"); b.textContent = "Mark handled";
      b.onclick = () => run(async () => { await api(`/assistant/requests/${encodeURIComponent(item.id)}/resolve`, {note: "Handled by owner in review page; no automatic reply."}); await load(); notice("Escalation marked handled. No reply sent."); });
      article.append(b);
    }
    $("question-results").append(article);
  });
  $("drafts").replaceChildren();
  drafts.forEach(d => {
    const b = document.createElement("button");
    b.textContent = `${d.status} · revision ${d.revision} · ${d.request.recipient_id} → ${d.destination.platform}/${d.destination.target} · ${d.created_at}`;
    b.onclick = () => run(async () => {
      if (dirty() && !confirm("Discard unsaved edits?")) return;
      await show(await api("/drafts/" + d.id)); notice("Draft loaded.");
    });
    $("drafts").append(b);
  });
}
async function show(draft) {
  selected = draft; $("review").hidden = false;
  $("draft-meta").textContent = `${draft.status} · revision ${draft.revision} · ${draft.destination.platform}/${draft.destination.target}`;
  $("draft-text").value = draft.text;
  $("draft-text").scrollTop = 0;
  $("warnings").textContent = draft.warnings.join("\n");
  $("evidence").textContent = JSON.stringify({ report: draft.report, evidence: draft.evidence }, null, 2);
  $("audit").textContent = JSON.stringify({ receipt: draft.receipt, events: await api(`/drafts/${draft.id}/audit`) }, null, 2);
  buttons();
}
$("login").onsubmit = e => { e.preventDefault(); run(async () => {
  apiKey = $("key").value.trim(); await load(); $("key").value = ""; notice("Connected. Check delivery mode before reviewing a draft.");
}); };
$("logout").onclick = () => location.reload();
$("refresh").onclick = () => run(async () => { await api("/refresh", {}); await load(); notice("Evidence refreshed. Existing draft text has not changed."); });
$("reload").onclick = () => run(async () => { await load(); notice("Reloaded."); });
$("persona-form").onsubmit = e => { e.preventDefault(); run(async () => {
  await api("/personas", {recipient_id: $("recipient").value, display_name: $("display-name").value,
    messages: $("samples").value.split("\n").map(s => s.trim()).filter(Boolean)});
  $("samples").value = ""; await load(); notice("Persona saved locally.");
}); };
$("draft-form").onsubmit = e => { e.preventDefault(); run(async () => {
  if (dirty() && !confirm("Discard unsaved edits?")) return;
  const draft = await api("/drafts", {recipient_id: $("draft-recipient").value,
    retrieval: {query: $("query").value, session_ids: $("sessions").value.split(",").map(s => s.trim()).filter(Boolean),
      since: $("since").value ? new Date($("since").value).toISOString() : null},
    destination: {platform: $("platform").value, target: $("target").value}});
  await load(); await show(draft); notice("Draft created. Nothing has been sent.");
}); };
async function act(name, extra = {}) {
  if (!selected) return;
  const draft = await api(`/drafts/${selected.id}/${name}`, {expected_revision: selected.revision, ...extra});
  await load(); await show(draft);
  notice(`Draft is ${draft.status}.`);
}
$("draft-text").oninput = buttons;
$("save-edit").onclick = () => run(() => act("edit", {text: $("draft-text").value}));
$("regenerate").onclick = () => run(() => act("regenerate"));
$("approve").onclick = () => run(() => act("decision", {action: "approve"}));
$("reject").onclick = () => run(() => act("decision", {action: "reject"}));
$("deliver").onclick = () => run(() => act("deliver"));
function openVoice(note) {
  voiceNote = note;
  $("voice-review").hidden = false;
  $("voice-transcript").value = note.transcript;
  $("voice-warning").textContent = note.warning;
  const confirmed = note.status === "confirmed";
  for (const id of ["voice-transcript", "voice-recipient", "voice-project", "voice-target"]) $(id).disabled = confirmed;
  $("voice-review").querySelector("button").textContent = confirmed ? "Retry pending draft" : "Create pending draft";
  if (confirmed) {
    $("voice-recipient").value = note.confirmed_request.recipient_id;
    $("voice-project").value = note.confirmed_request.project;
    $("voice-target").value = note.confirmed_request.destination.target;
    $("voice-warning").textContent += " Transcript confirmed. Retry generation with these saved details.";
  }
}
$("voice-file").onchange = () => { uploadId = null; };
$("voice-upload").onsubmit = e => { e.preventDefault(); run(async () => {
  const file = $("voice-file").files[0];
  if (!file || file.size > 8 * 1024 * 1024) throw new Error("Choose an audio file up to 8 MiB.");
  uploadId ||= crypto.randomUUID();
  notice(cloudVoice ? "Transcribing with ElevenLabs…" : "Transcribing locally… the first run may download model weights.");
  const response = await fetch(`/api/voice?request_id=${uploadId}`, {
    method: "POST", headers: {Authorization: "Bearer " + apiKey, "Content-Type": file.type || "application/octet-stream"}, body: file
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message || "Transcription failed.");
  await load(); openVoice(result); notice("Transcript ready. Check names, numbers, and negations before confirming.");
}); };
$("voice-review").onsubmit = e => { e.preventDefault(); run(async () => {
  if (!voiceNote) return;
  if (dirty() && !confirm("Discard unsaved draft edits?")) return;
  const payload = voiceNote.confirmed_request || {
    expected_revision: voiceNote.revision, transcript: $("voice-transcript").value,
    recipient_id: $("voice-recipient").value, project: $("voice-project").value,
    destination: {platform: "slack", target: $("voice-target").value}
  };
  let result;
  try { result = await api(`/voice/${voiceNote.id}/confirm`, payload); }
  catch (error) { openVoice(await api(`/voice/${voiceNote.id}`)); throw error; }
  voiceNote = result; $("voice-review").hidden = true;
  await load(); await show(await api(`/drafts/${result.draft_id}`)); notice("Voice draft is pending. Nothing has been sent.");
}); };
$("question-form").onsubmit = e => { e.preventDefault(); run(async () => {
  if (dirty() && !confirm("Discard unsaved draft edits?")) return;
  const project = $("question-project").value.trim();
  const payload = {
    question: $("question-text").value,
    recipient_id: $("question-recipient").value,
    retrieval: {project_ids: project ? [project] : null},
    destination: {platform: "slack", target: $("question-target").value}
  };
  const fingerprint = JSON.stringify(payload);
  if (questionRequest?.fingerprint !== fingerprint) questionRequest = {fingerprint, id: crypto.randomUUID()};
  const result = await api("/assistant/questions", {...payload, request_id: questionRequest.id});
  questionRequest = null;
  await load();
  if (result.draft_id) await show(await api(`/drafts/${result.draft_id}`));
  notice(result.status === "escalated" ? result.answer : "Answer draft awaits your review. Nothing sent.");
}); };
buttons();
