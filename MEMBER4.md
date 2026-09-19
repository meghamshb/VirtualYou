# Member 4 on develop: voice memos and owner escalation

This selectively integrates the voice and escalation work from
[PR #3](https://github.com/meghamshb2006/MakeNoMistake/pull/3), branch
`codex/member-4-voice-assistant` at `4f85fc9`, onto develop at `9cea5f8`.
It retains develop's persona service, recipient history, low-history formal
fallback, Git/Codex collection, optional GitHub observations, conversational RAG,
and owner-approved personal-DM replies. The inherited alternative Member 2
implementation and the separate unauthenticated ElevenLabs diagnostic page were
not imported. This is a selective port, not an ancestry merge of that branch.

## Data flow

Owner recording → speech-to-text → redaction → transcript correction/confirmation
→ project-scoped ActivityRecord → existing evidence index and report generator
→ owner approval → user-token delivery in the recipient's human DM.

Colleague DM → existing DMInbox/DMReplies → existing conversational RAG engine
+ recipient style → either a private approval card or a persisted Needs attention
item. Judgment/commitment questions and missing evidence do not send automatic
acknowledgments or answers to the colleague. No additional LLM classifier call is
added. Factual questions are not restricted to a small phrase whitelist.

Voice evidence is labelled **user-reported, not independently verified**. The
voice normalizer explicitly disables local Git overlay and GitHub enrichment so
a spoken memo cannot inherit unrelated repository observations. Other ingestion
sources keep their existing GitHub enrichment behavior.

## Install and configure once

From the repository, in the environment used to run the Slack agent:

```sh
python -m pip install -e '.[backend,voice]'
python -m pip install -e ./slack_agent
```

Keep the existing text-model configuration, including GPT-4o mini. Local voice
transcription defaults to faster-whisper `small` on CPU with int8 weights:

```dotenv
VIRTUAL_YOU_VOICE_PROVIDER=local
VIRTUAL_YOU_VOICE_MODEL=small
VIRTUAL_YOU_VOICE_LANGUAGE=
```

Weights may download on first use. They are cached and the loaded model is reused.
For an explicit cloud alternative, install `.[backend,voice-cloud]` instead and set
`VIRTUAL_YOU_VOICE_PROVIDER=elevenlabs` and `ELEVENLABS_API_KEY` on the server. That
option uploads the original audio to ElevenLabs `scribe_v2`; text redaction happens
after transcription. No automatic provider switch or cloud retry is performed.

Update the Slack app using the checked-in manifest and reconnect through
`/slack/install` to grant the new bot `files:read` scope. Keep the existing user
`im:history`, `im:read`, and `chat:write` grants. The bot reads owner audio shared
with it; the user token sends approved answers in human DMs.

Enable Voice in Setup, assign permitted projects to a recipient, and review their
existing style. No persona files need to be regenerated for this integration.

## Slack and browser review

Run the existing `slack_agent/app_oauth.py` entry point. It now serves Slack
events/OAuth and the backend review/API on one server, port 3000 by default:

- `/slack/events`: signed Slack events and interactions.
- `/slack/install` and `/slack/oauth_redirect`: authorization.
- `/review#voice`: optional browser recording and transcript review.
- `/api/*`: owner backend-key authentication, including voice and escalation.
- Existing GitHub authorization routes remain available.

Only one process may use the data directory. Do not run the standalone backend
against it simultaneously. Socket Mode's legacy headless runtime still works for
Slack attachments, but does not host the browser review page.

In Slack, attach an audio memo you own to your VirtualYou DM. App Home lists pending
voice memos and Needs attention items. The optional Record memo button opens the
browser companion through the configured HTTPS origin. Review its transcript,
select an allowed project and recipient, and confirm. This produces a pending
draft; it does not grant approval. The eventual approval card identifies owner
delivery. Existing automatic report destinations retain their configured behavior.

The browser can record and preview before login. Transcription requires the backend
access key from `virtual-you-server show-key` run with the same data-directory
configuration. It is not the OpenAI or ElevenLabs key. The browser retains this
key only in tab memory. Audio is capped at 8 MiB and 3 minutes; the app does not
persist audio files. Corrected transcripts are stored locally as private state.

## Synchronization and failure behavior

- Stable upload/confirmation IDs prevent duplicate activities and drafts on retries.
- Voice processing has a separate worker lane from Slack owner actions.
- Independent questions use per-request locks; they do not wait behind a global
  generation lock. Existing per-recipient DM workers remain in place.
- Transcript confirmation checks current voice preferences, persona review,
  recipient destination, and project permissions, including browser/API requests.
- Approval and delivery check evidence hashes and current Slack audience policy.
  Historical evidence remains usable for historical questions; current-status
  answers require recent support.
- Owner delivery checks the authorized user/workspace and user `chat:write` scope.
  It never falls back to the bot. Ambiguous sends are not automatically replayed.
- Escalations persist across restarts. Mark handled records a resolution without
  sending anything. Supply new work context through normal ingestion or a reviewed
  voice memo before asking for a new answer.
- A failed Slack worker makes health/events return 503. The HTTP entry point exits
  so an existing service supervisor can restart it; it does not acknowledge work
  while the listener is stopped.
- A standalone HTTP backend cannot deliver owner-bound drafts without the connected
  Slack sender. Persisted audience guards still reject invalid approval attempts.

## Validation

Tests cover the actual existing persona/retrieval paths, broad factual questions,
judgment and missing-evidence escalation, historical evidence, scope revocation,
duplicate events, user-token delivery, voice confirmation/restart, decoded audio
limits, mocked cloud errors, and the combined OAuth/review server. New tests also
verify that a voice memo does not receive unrelated GitHub observations and that
slow questions/voice jobs do not monopolize unrelated work.

Run backend tests from the repository and Slack tests with its package on the path:

```sh
python -m pytest tests
PYTHONPATH=src:slack_agent python -m pytest slack_agent/tests
```

Browser QA uses a separate synthetic server and fake microphone/STT to exercise
record, preview, transcribe, correct, confirm, approve, simulated delivery, and
escalation resolution at desktop/mobile sizes. It does not send Slack messages.

Live workspace file access requires reauthorization and a real recording trial.
Real-time calls, synthesized speech, a distributable installer, and continuous
remote hosting are not part of this change. A locally hosted listener still needs
an awake, connected machine.
