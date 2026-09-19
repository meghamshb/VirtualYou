# Member 4 — voice memos and asynchronous work questions

Leo's implementation adds voice memo → transcript review → normalized activity →
pending draft, plus evidence-checked work questions and a persistent escalation
inbox. It reuses Member 1's `IngestionService`, Member 2's recipient profile and
Member 3's `DraftEngine` / approval workflow. No separate text-generation engine
or automatic answer delivery is introduced.

## Run voice input

Use Python 3.12 and choose one transcription option. The backend and Slack worker
must share the same private data directory and normalized activity directory.
Run only one owning process against that SQLite directory.

**Local, no speech API charges (default):**

```bash
uv sync --extra backend --extra dev --extra voice --python 3.12
# Set in your private .env:
# VIRTUAL_YOU_VOICE_PROVIDER=local
# VIRTUAL_YOU_VOICE_MODEL=small
# VIRTUAL_YOU_VOICE_LANGUAGE=en   # optional; omit for language detection
uv run --no-sync virtual-you-server serve
```

`faster-whisper` runs on the backend CPU with int8 weights. First use downloads
model weights; subsequent transcription works with the cached model. Original
audio is decoded in memory and is not stored by this app. Runtime/CPU cost still
applies; this is not a hosted free transcription service.

**ElevenLabs Scribe, using your account's allowance:**

```bash
uv sync --extra backend --extra dev --extra voice-cloud --python 3.12
# Set in your private .env, never in Git or browser JavaScript:
# VIRTUAL_YOU_VOICE_PROVIDER=elevenlabs
# ELEVENLABS_API_KEY=<your locally stored key with Speech to Text permission>
uv run --no-sync virtual-you-server serve
```

This explicitly sends the original audio to ElevenLabs `scribe_v2`. It cannot
redact words before transcription. Returned text is redacted locally before
persistence and drafting. The review page and Slack Home disclose the configured
processing location. Provider retention settings are separate; this integration
does not promise zero retention. Missing keys, exhausted allowance, rate limits
and provider errors fail visibly, without an automatic retry or provider switch.
API access is included on the Free plan, but actual balance and permissions must
be checked in the account. No account allowance was verified during development.
Restart after changing providers.

The existing `Dockerfile.backend` supports `--build-arg BACKEND_EXTRAS=backend,voice`
or `backend,voice-cloud` to include the chosen optional dependency. Mount a
persistent model cache for local Whisper if repeated weight downloads are unwanted.

## Owner review flow

1. Open the backend review page and connect with its owner key. Create or choose
   a recipient profile first. `virtual-you-server seed-demo` can supply synthetic
   personas and activity for a local demo; delivery defaults to simulation.
2. Upload a voice memo, at most **3 minutes / 8 MiB**. WAV, MP3, M4A, OGG/Opus,
   WebM and other formats supported by the installed decoder are accepted.
3. Correct the transcript, especially names, numbers and negations. Choose its
   project, recipient and destination. Before confirmation it is not indexed.
4. Confirm to run Member 1's normalizer and create a pending Member 3 draft.
   Voice activity is labeled **user-reported, not independently verified**;
   the application's own Git state is not overlaid onto the memo.
5. Review/edit the draft, approve that revision, then deliver. Edits invalidate
   approval. Confirmation itself never approves or sends a message.

Repeated upload/confirmation IDs do not create duplicate activity or drafts.
If text generation fails after confirmation, reopen the note and retry its saved
transcript, recipient and destination. To change a confirmed memo, start a new
memo; an earlier pending draft can be rejected. No TTS, cloned speaking voice,
realtime conversation or in-page microphone recorder is included in this version.

## Slack flow

Install the optional transcription dependency in the Slack worker's Python
environment as well (`pip install -e '..[backend,voice]'` or
`'..[backend,voice-cloud]'` from `slack_agent`). Follow
[WORKFLOW.md](slack_agent/WORKFLOW.md) for OAuth, events and persistent startup.
Update the manifest and **reauthorize for the added bot `files:read` scope**.

- The owner sends an audio file to their VirtualYou bot DM. A signed bot-authorized
  owner event queues transcription; downloads require an owner-owned audio file
  and an official Slack private-file URL. Slack still retains the uploaded file.
- Open Home → **Review voice memo**, correct its transcript, and choose a ready
  recipient and an already allowed project. Enable **Voice** in sources first.
  The 3,000-character Slack editor limit is explicit; longer transcripts use the
  web page/API without silent truncation.
- Enable **reply assistance** for a recipient with a reviewed style and allowed
  projects. Their bot DM, their mention in a channel accessible to the bot, or
  the owner-invoked **Draft update for request** shortcut queues a work question.
  Signed user DM events also work when subscribed; an enabled DM poller uses
  the same request identity for recovery, preventing duplicate work drafts.
- Four conservative English intents are supported: **completed work, changes,
  blockers, current status**. Examples: “What was completed today?”, “What
  changed?”, “Any blockers?”, “What is the current status?”. This is an explicit
  phrase classifier, not general natural-language intent understanding.
- Scope decisions, commitments, deadlines, personal opinions, compound/unrecognized
  questions and missing/stale evidence escalate. The owner sees the original
  redacted question, reason and “I don't have enough information…” in Home and
  the web page. **Mark handled** records resolution; it sends no answer.
- Supported questions retrieve only that recipient's allowed projects and enabled
  sources, then use the shared draft engine. The owner receives the review card.
  On approval, this work-answer route delivers as **VirtualYou to the recipient's
  bot DM**, including when the question originated in a channel/human DM. It does
  not post automatically into the original thread. Other existing personal-chat
  reply features retain their separate behavior.

Freshness is checked before generation and again at approval/delivery (default
24 hours). Changed evidence, moved projects, changed styles, revoked source/reply
permissions or a global pause block older Slack work drafts. These checks also
run through the HTTP gate after restarting without the Slack coordinator.
An offline owner can accumulate questions and review them later; approval is
still required for every recipient-facing answer. The worker must remain awake,
online and reachable for events. No always-on hosting was deployed by this change.

## Owner-only API

All endpoints require the existing owner bearer key. Never expose it to managers.
Use UUID request IDs and preserve them when retrying an identical operation.

| Endpoint | Input / behavior |
| --- | --- |
| `POST /api/voice?request_id=<uuid>` | Raw audio request body, not multipart; returns a reviewable note |
| `GET /api/voice` / `GET /api/voice/{id}` | Recent notes / one note |
| `POST /api/voice/{id}/edit` | `expected_revision`, corrected `transcript` |
| `POST /api/voice/{id}/confirm` | `expected_revision`, optional corrected `transcript`, `recipient_id`, `project`, `destination` |
| `POST /api/assistant/questions` | `request_id`, `question`, `recipient_id`, `retrieval` scope, `destination` |
| `GET /api/assistant/requests` | Recent questions, drafts and escalation states |
| `POST /api/assistant/requests/{id}/resolve` | Owner resolution `note`; no delivery |

The question endpoint also supports the shared Discord destination adapter, but
this change implements **Slack inbound events only**. Discord gateway listening
and realtime voice remain follow-up work, not completed features.

## Validation and remaining acceptance

See [docs/STATUS.md](docs/STATUS.md) for exact current checks. Tests use synthetic
activity and fake Slack/cloud transports. A real local Whisper transcription and
a desktop/mobile browser voice → review → draft flow were exercised. A live
ElevenLabs Scribe v2 request also transcribed the 8.5-second synthetic memo
successfully (HTTP 200, 1.15 seconds for one request). Account balance was not
queried. Representative recordings, Slack authorization/file download/mention
delivery, real-text-model factual/style quality and continuous host uptime still
need acceptance in the team's configured environment. Quote validation does not prove
semantic entailment; the owner must review factual claims.

References: [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[ElevenLabs speech-to-text API](https://elevenlabs.io/docs/api-reference/speech-to-text/convert),
[Free-plan API access](https://help.elevenlabs.io/hc/en-us/articles/28184926326033-How-much-does-it-cost-to-use-the-API),
[Slack mentions](https://docs.slack.dev/reference/events/app_mention/),
[Slack private files](https://docs.slack.dev/reference/objects/file-object/).
