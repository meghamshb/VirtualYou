"use strict";

const el = id => document.getElementById(id);
let busy = false;
let configured = false;
const recorder = new VoiceMemoRecorder(render);

function render() {
  recorder.setLocked(busy, true);
  el("voice-transcribe").disabled ||= !configured;
  el("voice-file").disabled = busy || recorder.active;
  el("file-transcribe").disabled = busy || recorder.active || !configured || !el("voice-file").files[0];
}

async function readResponse(response) {
  let body;
  try { body = await response.json(); }
  catch { throw new Error("The local server returned an unreadable response. Your recording is still available."); }
  if (!response.ok) throw new Error(body.error?.message || body.detail || `Local server error (${response.status}).`);
  return body;
}

async function transcribe(blob) {
  if (busy || recorder.active || !configured || !blob) return;
  if (!blob.size || blob.size > 8 * 1024 * 1024) {
    el("test-notice").textContent = "Choose a nonempty recording up to 8 MiB.";
    return;
  }
  busy = true;
  el("voice-playback").pause();
  el("test-result").hidden = true;
  el("test-notice").textContent = "Uploading and transcribing with ElevenLabs… Your clip stays here while you wait.";
  render();
  try {
    const response = await fetch("/dev/voice/transcribe", {
      method: "POST",
      headers: {"Content-Type": "application/octet-stream", "X-Virtual-You-Test": "1"},
      body: blob,
    });
    const result = await readResponse(response);
    const body = result.response;
    const text = body && typeof body.text === "string" ? body.text : "";
    const success = result.provider_http_status >= 200 && result.provider_http_status < 300;
    el("result-meta").textContent = `ElevenLabs HTTP ${result.provider_http_status} · ${result.elapsed_seconds}s elapsed · ${Number(result.duration_seconds).toFixed(1)}s audio`;
    el("result-transcript").textContent = text || "No transcript returned. See the provider response below.";
    el("result-format").textContent = `Response format: ${result.response_format}`;
    el("result-json").textContent = typeof body === "string" ? body : JSON.stringify(body, null, 2);
    el("test-result").hidden = false;
    el("test-notice").textContent = !success
      ? `ElevenLabs returned HTTP ${result.provider_http_status}. Its error details are below. No automatic retry was made.`
      : text.trim()
        ? "ElevenLabs responded. Review its transcript and response below."
        : "ElevenLabs responded but returned no speech text. Check the playback and microphone, then try again if needed. Its response is below.";
  } catch (error) {
    el("test-notice").textContent = error instanceof TypeError
      ? "Could not reach the local server. Your clip is still here. Check that the app is running before trying again; no automatic retry was made."
      : error.message;
  } finally {
    busy = false;
    render();
  }
}

el("voice-transcribe").onclick = () => transcribe(recorder.clip?.blob);
el("voice-file").onchange = render;
el("voice-upload").onsubmit = event => {
  event.preventDefault();
  transcribe(el("voice-file").files[0]);
};

async function init() {
  render();
  if (location.protocol === "file:") {
    el("test-setup").textContent = "Open http://127.0.0.1:8000/ in your browser to use the running app.";
    return;
  }
  try {
    const status = await readResponse(await fetch("/dev/voice/status"));
    configured = status.configured;
    el("test-setup").textContent = configured
      ? "ElevenLabs Scribe v2 is configured. Ready to test."
      : "The server needs ELEVENLABS_API_KEY in its local .env file. Restart with --voice-test after configuring it.";
  } catch (error) {
    el("test-setup").textContent = `Audio test unavailable: ${error.message} Start the local server with --voice-test.`;
  }
  render();
}
init();
