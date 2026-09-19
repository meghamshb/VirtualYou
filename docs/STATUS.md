# Member 4 delivery status — 2026-09-19

Branch: `codex/member-4-voice-assistant`.
PR target: `feat/member-2-persona-phase-1.1` (Member 2 PR #2).

## Integration basis

Started from Member 2's phase 1.1 branch at `d2ec671`, then merged the current
`phase-2-slack-experience` at `375683e` in integration commit `0c813c5`.
That brings the teammate's OAuth/recipient controls and personal-DM workflow.
Those inherited Slack changes are dependencies, not all new Member 4 work.
The persona conflict preserves canonical/private soul exports and style editing;
removing retained examples remains supported. Codex is accepted in Slack filters.

A later fetch found `develop` at `6faf146` with further collection/reply changes.
**Leo explicitly chose to keep this PR based on the phase 1.1 / Member 2 branch.**
`develop` is not merged here; integration with it requires a separate review,
particularly around `DraftEngine`, retrieval and personal-DM question routing.
Member 1's ingestion source, CLI, ActivityRecord contract, fixtures/tests and
root Dockerfile remain unchanged from phase 1.1 `ab3c90f`.

## Implemented

- Browser Record/Stop, timer, playback preview and explicit transcription;
  permission/device errors and upload fallback. Cancelling or stopping releases
  microphone tracks. Capture stops at 2:59 or when the tab is hidden. Blob audio
  preview is allowed by a narrowly scoped `media-src` security-policy directive.
- Voice upload with bounded decoding (3 minutes / 8 MiB), local Whisper by
  default and optional ElevenLabs Scribe. Cloud processing is explicitly shown.
- Redacted, editable transcript review before shared Member 1 normalization.
  Stable memo/activity/draft identities, saved confirmation details and resumable
  generation prevent duplicates. Voice claims stay labeled user-reported.
- Shared draft generation and approval for completed work, changes, blockers
  and current status. This is a conservative English phrase classifier.
- Persisted, owner-visible escalations for decisions, commitments, deadlines,
  opinions, unsupported questions and missing/stale evidence. Resolution sends nothing.
- Slack mention/DM/shortcut routing, owner-only audio uploads, event/poll
  deduplication, review modals and Home escalation list. Added `files:read`.
- Evidence and persisted Slack audience checks at both approval and delivery,
  including a restart with only the HTTP backend. No automatic answer sending.
- Web voice/question review controls, optional install extras, setup/API guide
  in [MEMBER4.md](../MEMBER4.md), and optional backend Docker build extras.

## Verified locally

- Root suite: **220 passed** on Python 3.12, including **31 new Member 4 tests**.
- Slack coordinator/history/experience/DM/Member 4 suites: **60 passed**,
  including **18 new Member 4 tests**, with fake Slack clients/transports.
- Both suites report two inherited FastAPI/Starlette deprecation warnings.
- Real local `faster-whisper` small/int8 transcription of an 8.5-second synthetic
  English memo preserved both its blocker and “deadline has not been confirmed”.
  First run was 26.4 seconds including model setup; this is one measurement.
- Live ElevenLabs Scribe v2: **HTTP 200** on the same 8.5-second synthetic
  English memo, **1.15 seconds** for this one request. The returned transcript
  preserved the blocker and unconfirmed deadline. This verifies this key's
  transcription access at test time, not account balance or a latency guarantee.
  Both local entrypoint environments now select ElevenLabs; keys stay in
  ignored mode-600 files and are not included in the commit.
- Browser: `http://127.0.0.1:8127`, installed Chrome through Playwright; Browser
  plugin unavailable, bundled Playwright browser absent. Desktop **1280×900**
  and mobile **390×844**. Correct URL/title, meaningful content, no error
  overlay, no console errors, no horizontal mobile overflow; screenshots inspected.
- Real audio upload → local transcript → correction → normalized pending draft;
  pending delivery disabled; supported blocker answer; approval then edit reset
  approval; deadline question escalated; owner resolution produced no send.
  Text drafting used the clearly labeled demo provider and delivery stayed simulated.
- Ruff lint/format and whitespace checks passed on the scoped changes. Source
  comparison confirms unchanged phase 1.1 ingestion. Temporary audio, screenshots,
  browser scripts and runtime databases are outside Git under `/tmp`.

### Recorder follow-up verification

- Re-ran the root suite (**220 passed**) and the six scoped Slack suites
  (**60 passed**) after the recorder/security-header change; the same two
  inherited deprecation warnings remain. Scoped Ruff and JavaScript syntax pass.
- Chrome via existing Playwright at `http://127.0.0.1:8128`, isolated synthetic
  data, 1280×900 and 390×844. Browser plugin not available. Correct URL/title,
  meaningful page content, no error overlay or unexpected console errors; desktop,
  mobile and pending-draft screenshots inspected, no horizontal overflow.
- Native `MediaRecorder` captured a synthetic microphone feed, producing playable
  WebM. Stop released the microphone and showed a preview with **no upload until
  Transcribe**. Real local Whisper transcribed it; correction, normalization and
  pending-draft generation passed. Pending delivery stayed disabled.
- Cancellation, late microphone permission after cancellation, denied/missing/busy
  microphone, unsupported browser fallback, empty capture, recorder failure,
  construction/start failure, oversized audio, explicit same-ID network retry,
  reopening an existing draft and protecting unsaved edits passed. Error cases
  inject browser failures; the retry response uses a fixture. File-upload fallback
  also passed with real local Whisper. No physical microphone was recorded.
- Automatic 2:59 stopping used an accelerated browser clock; leaving-tab stopping
  used a simulated visibility event. Both release tracks and preserve the preview.
- The same **8.58-second browser WebM** transcribed successfully with the configured
  live ElevenLabs Scribe adapter in **1.10 seconds** for one request, preserving
  the blocker and unconfirmed deadline. The quota balance was not queried.
- Restarted the user's port-8000 review backend with the preview security policy;
  health, recorder asset and configured ElevenLabs status were checked. Text
  generation stays demo/extractive and delivery stays simulated. Safari, physical
  microphones, real mobile devices and in-app browser permission UI remain untested.
- Browser scripts/results/screenshots and synthetic audio remain outside Git under
  `/tmp/makenomistake-recorder-qa/`. No frontend dependency was added.

### Record button connection fix

The first recorder UI unnecessarily disabled local capture until backend login.
A page refresh clears that in-memory login, leaving Record disabled. Capture,
playback, discard and file selection now work before login; only transcription
requires a successful connection. An adjacent link explains this and leads to
the login form. Connecting preserves the recorded clip or chosen file.

Verified the reported state and the enabled Record button after the fix in Leo's
actual in-app browser at port 8000, with no browser console errors. Isolated Chrome
QA at port 8128 (1280×900 and 390×844) exercised pre-login recording/playback,
zero automatic uploads, clip preservation through login, authenticated explicit
transcription, refresh, file-selection persistence and unsupported-browser
fallback. Synthetic microphone input and a mocked transcription response were
used; no real microphone audio or new provider call was used for this regression.
Desktop/mobile screenshots were inspected with no horizontal overflow. JavaScript
syntax and whitespace checks pass. The Python backend is unchanged in this fix;
the 220/60 regression totals above are from the preceding recorder implementation.

### Transcription connection and microphone feedback follow-up

- A ready memo's signed-out transcription button now says **Connect to transcribe**
  and opens an inline login form instead of staying disabled. The file-upload path
  uses the same flow. Cancelled/failed connection and HTTP 401 preserve the audio;
  successful login clears the key field and waits for an explicit transcription
  click after showing the provider. No authentication or approval rule changed.
- Added a local Web Audio input meter over the same microphone stream. Its live
  level and sound/quiet/loud feedback come from sampled audio, not animation.
  Quiet input warns after 2.5 seconds. Stop/cancel closes the analyser/context and
  microphone. Unsupported meters leave recording and playback available.
- Isolated Chrome QA at port 8128, 1280×900 and 390×844: native input meter reacted
  to synthetic speech; a separate silent WAV stayed at zero and produced the quiet
  warning. Connection cancel/wrong-key/success/expired-key, clip retention, no
  automatic upload, explicit transcription, file-upload entry and unavailable
  meter fallback passed. Real local Whisper transcribed the recorded audio;
  correction produced a pending draft with delivery disabled. Screenshots were
  inspected, no horizontal overflow or unexpected browser errors occurred.
- QA scripts, screenshots and synthetic audio are under
  `/tmp/makenomistake-audio-feedback/`, outside Git. JavaScript syntax/whitespace
  checks pass. No physical microphone capture or new ElevenLabs request was used.
  These are frontend changes; the earlier 220/60 Python totals were not rerun.

### No-login ElevenLabs test mode

- `virtual-you-server serve --voice-test` opens a dedicated local audio tester at
  `/` and `/voice-test`. Record/Stop/preview or file upload → explicit Transcribe
  → transcript, provider HTTP status, elapsed time and returned JSON. This path
  needs no backend key and creates no voice note, activity, draft or delivery.
  Credentials stay server-side; returned credential-like strings are redacted.
- The mode is off by default. CLI requires loopback and disables proxy-header
  trust. Diagnostic routes check loopback peer, localhost Host, exact Origin and
  a custom upload header. `/review` retains the full dashboard and all existing
  `/api/*` routes still require the owner key. Both paths reuse the same adapter;
  normal transcription keeps its existing error mapping and approval flow.
- **245 root tests passed**, including 25 new diagnostic-route/CLI checks, and
  **60 scoped Slack tests passed**. The two inherited deprecation warnings remain.
  Covered disabled mode, normal API authentication, nonlocal/foreign-origin/null-
  origin/rebinding requests, audio bounds, provider status/body/error exposure,
  secret redaction, network failure, no retry and no saved application records.
- Chrome via Playwright, isolated `http://127.0.0.1:8128/`, 1280×900 and 390×844:
  page identity/content, no overlay, live input meter, recording/preview with no
  login, no upload before Transcribe, provider JSON matching, file fallback,
  double-click protection, clip retention, inert provider markup and errors pass.
  Provider 429 and local 503 UI cases use fixtures. No unexpected console errors;
  the injected 503 produces one expected resource error. Desktop/mobile screenshots
  were inspected with no overflow. Browser plugin not available; installed Chrome
  and the existing Playwright runtime were used, with no new dependencies.
- Two live synthetic-audio requests were made. The first returned HTTP 200 with
  empty speech text; it was not counted as a successful transcript and was not
  automatically retried. A separate 8.40-second native Chrome recording returned
  the correct blocker and unconfirmed-deadline transcript, language and word
  timestamps in **1.13 seconds**. Empty speech responses now show an explicit
  message alongside the provider body. This is a smoke test, not an accuracy or
  latency guarantee; no physical microphone was captured or account quota queried.
- QA scripts, captured synthetic audio, response JSON and screenshots remain under
  `/tmp/makenomistake-voice-test/`, outside Git. The port-8000 server was restarted
  with `--voice-test`; a fresh in-app browser tab shows ElevenLabs configured and
  Record enabled, with no console errors. Existing tabs were not reloaded.

## Remaining live acceptance and limitations

Live ElevenLabs transcription now passes the synthetic smoke test. Remaining
speech acceptance is representative user recordings, accent/noise accuracy and
account allowance monitoring; the balance was not queried. Reauthorize the
Slack app's new `files:read`, then test an actual owner memo and recipient mention
through review to one approved destination. No real Slack/Discord messages were sent
in this implementation run. Persona/factual quality still needs the selected
live text model and human acceptance from [MEMBER2_ACCEPTANCE.md](../MEMBER2_ACCEPTANCE.md).

The standing worker/startup service is reused, not deployed or proven available
24/7. The Mac/host must stay awake and online. Docker builds were not run.
Discord's shared outbound adapter remains available; Discord inbound events and
spoken responses/realtime voice are unimplemented. Video/slides remain paused by
Leo. The in-page microphone recorder is now implemented. Voice recordings are not persisted by this app;
Slack/cloud retention is controlled by those providers. No raw media, credentials,
private persona samples or concept-video files are included in the PR.

The [allocation audit](ALLOCATION_AUDIT.md) checks every Member 4 task and the
earlier Member 2 handoff. It explicitly retains Discord incoming events,
continuous hosting, live Slack acceptance and live-model persona quality as
unfinished work. The port-8000 review server alone does not run the Slack worker.
