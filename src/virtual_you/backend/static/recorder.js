"use strict";

// Capture and preview only. The app's existing upload/review path owns transcription.
class VoiceMemoRecorder {
  constructor(onChange) {
    this.onChange = onChange;
    this.phase = "idle";
    this.locked = true;
    this.generation = 0;
    this.clip = null;
    this.stream = null;
    this.recorder = null;
    this.previewUrl = null;
    this.supported = window.isSecureContext && !!navigator.mediaDevices?.getUserMedia
      && typeof MediaRecorder !== "undefined";
    this.el = id => document.getElementById(id);
    this.el("voice-start").onclick = () => this.start();
    this.el("voice-stop").onclick = () => this.stop();
    this.el("voice-discard").onclick = () => this.discard();
    window.addEventListener("pagehide", () => this.discard());
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && this.phase === "recording") this.stop("Recording stopped when you left this tab.");
      else if (document.hidden && this.phase === "requesting") this.discard();
    });
    if (!this.supported) this.message("Microphone recording is unavailable here. Open this page in Chrome or Safari over HTTPS (or localhost), or upload an audio file below.");
  }

  get active() { return ["requesting", "recording", "stopping"].includes(this.phase); }
  message(text) { this.el("voice-status").textContent = text; }

  render() {
    this.el("voice-recorder").dataset.state = this.phase;
    this.el("voice-start").disabled = this.locked || this.active || !this.supported;
    this.el("voice-start").textContent = this.clip ? "Record again" : "Record";
    this.el("voice-stop").hidden = !["recording", "stopping"].includes(this.phase);
    this.el("voice-stop").disabled = this.phase !== "recording";
    this.el("voice-discard").hidden = !this.active && !this.clip;
    this.el("voice-discard").disabled = this.locked && !this.active;
    this.el("voice-discard").textContent = this.active ? "Cancel recording" : "Discard recording";
    this.el("voice-preview").hidden = !this.clip;
    this.el("voice-transcribe").disabled = this.locked || this.active || !this.clip;
  }

  setLocked(locked) { this.locked = locked; this.render(); }
  changed() { this.render(); this.onChange(); }

  releaseMicrophone() {
    clearInterval(this.tick);
    clearTimeout(this.deadline);
    this.stream?.getTracks().forEach(track => track.stop());
    this.stream = null;
  }

  clearClip() {
    this.el("voice-playback").pause();
    this.el("voice-playback").removeAttribute("src");
    this.el("voice-playback").load();
    if (this.previewUrl) URL.revokeObjectURL(this.previewUrl);
    this.previewUrl = null;
    this.clip = null;
  }

  discard(text = "Recording discarded. You can record again or upload a file.") {
    this.generation += 1; // Ignore a late permission result or final recorder event.
    if (this.recorder && this.recorder.state !== "inactive") this.recorder.stop();
    this.recorder = null;
    this.releaseMicrophone();
    this.clearClip();
    this.phase = "idle";
    this.el("voice-timer").textContent = "0:00";
    this.message(text);
    this.changed();
  }

  async start() {
    if (this.locked || this.active || !this.supported) return;
    if (this.clip && !window.confirm("Discard this recording and record a new memo?")) return;
    const generation = ++this.generation;
    this.el("voice-playback").pause();
    this.phase = "requesting";
    this.message("Allow microphone access in your browser. Nothing is uploaded while recording.");
    this.changed();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio: true});
      if (generation !== this.generation) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }
      this.stream = stream;
      const mimeType = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus", "audio/webm"]
        .find(type => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(stream, {audioBitsPerSecond: 64000, ...(mimeType ? {mimeType} : {})});
      this.recorder = recorder;
      const chunks = [];
      let bytes = 0;
      recorder.ondataavailable = event => {
        if (generation !== this.generation || !event.data.size) return;
        bytes += event.data.size;
        if (bytes > 8 * 1024 * 1024) {
          this.discard("Recording exceeded 8 MiB. Please record a shorter memo.");
          return;
        }
        chunks.push(event.data);
      };
      recorder.onerror = () => {
        if (generation === this.generation) this.discard("Recording failed. Check your microphone and try again, or upload an audio file.");
      };
      recorder.onstop = () => {
        if (generation !== this.generation) return;
        this.releaseMicrophone();
        this.recorder = null;
        const blob = new Blob(chunks, {type: recorder.mimeType || chunks[0]?.type || "application/octet-stream"});
        if (!blob.size) {
          this.discard("No audio was captured. Record again and speak before pressing Stop.");
          return;
        }
        this.clip = {blob, requestId: crypto.randomUUID()};
        this.previewUrl = URL.createObjectURL(blob);
        this.el("voice-playback").src = this.previewUrl;
        this.phase = "ready";
        this.message((this.stopReason || "Recording ready.") + " Preview it, then choose Transcribe this memo.");
        this.changed();
      };
      stream.getAudioTracks().forEach(track => track.addEventListener("ended", () => {
        if (generation === this.generation && this.phase === "recording") this.stop("Microphone disconnected. Check the captured audio.");
      }));
      recorder.start(1000);
      this.clearClip();
      this.stopReason = "";
      this.startedAt = performance.now();
      this.phase = "recording";
      this.el("voice-timer").textContent = "0:00";
      this.message("Recording… Speak your update, then press Stop recording.");
      // Leave a one-second buffer below the backend's decoded 180-second limit.
      const updateTimer = () => {
        const seconds = Math.floor((performance.now() - this.startedAt) / 1000);
        this.el("voice-timer").textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
        if (seconds >= 179) this.stop("Recording reached the 3-minute limit.");
      };
      this.tick = setInterval(updateTimer, 250);
      this.deadline = setTimeout(() => this.stop("Recording reached the 3-minute limit."), 179000);
      this.changed();
    } catch (error) {
      if (generation !== this.generation) return;
      this.releaseMicrophone();
      this.recorder = null;
      this.phase = this.clip ? "ready" : "idle";
      const messages = {
        NotAllowedError: "Microphone permission was denied. Allow access in browser settings and try again, or upload an audio file.",
        NotFoundError: "No microphone was found. Connect one and try again, or upload an audio file.",
        NotReadableError: "The microphone is unavailable or in use. Close other recording apps and try again, or upload an audio file.",
        SecurityError: "This browser blocks microphone access. Open the page in Chrome or Safari, or upload an audio file.",
      };
      this.message(messages[error.name] || "Could not start recording. Check your microphone or upload an audio file.");
      this.changed();
    }
  }

  stop(reason = "Recording ready.") {
    if (this.phase !== "recording") return;
    this.phase = "stopping";
    this.stopReason = reason;
    this.message("Finishing recording…");
    // stop queues the final audio chunk; release hardware immediately afterwards.
    if (this.recorder.state !== "inactive") this.recorder.stop();
    this.releaseMicrophone();
    this.changed();
  }
}
