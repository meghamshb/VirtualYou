"use strict";
const $ = (id) => document.getElementById(id);
let apiKey = "", selected = null, busy = false;
function notice(text) { $("notice").textContent = text; }
function githubStatus(github) {
  $("github-status").textContent = github.connected
    ? `Connected as ${github.login || "GitHub"} · repo ${github.repo || "(set VIRTUAL_YOU_GITHUB_REPO)"}`
    : "Not connected. Click Authorize GitHub — do not paste a token.";
}
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
  const github = await fetch("/github/status").then(r => r.json()).catch(() => ({connected:false}));
  githubStatus(github);
  const [status, personas, drafts] = await Promise.all([api("/status"), api("/personas"), api("/drafts")]);
  $("status").textContent = JSON.stringify(status, null, 2);
  $("personas").textContent = personas.map(p => `${p.display_name} (${p.recipient_id}) · version ${p.version} · ${p.style.formality}`).join("\n") || "No personas yet.";
  const previous = $("draft-recipient").value;
  $("draft-recipient").replaceChildren();
  personas.forEach(p => { const o = document.createElement("option"); o.value = p.recipient_id; o.textContent = p.display_name; $("draft-recipient").append(o); });
  if (personas.some(p => p.recipient_id === previous)) $("draft-recipient").value = previous;
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
buttons();
fetch("/github/status").then(r => r.json()).then(githubStatus).catch(() => githubStatus({connected:false}));
