> **Integration update (2026-09-20):** `leo-dev` now reconciles PR #3's ancestry
> and restores its explicit loopback-only `--voice-test` diagnostics. It retains
> the newer scoped voice/assistant implementation described below. PR #2's portable
> persona tools are also integrated while preserving sparse-history and style learning.
> See [the integration audit](docs/LEO_INTEGRATION.md). The next section records the
> earlier selective port into `develop`, before this integration.

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

### Incoming colleague voice questions

The personal-DM listener also accepts one audio attachment from a colleague.
Enable Voice in source settings and reconnect Slack after adding user-level
`files:read` (the bot's file permission alone cannot read a human-to-human DM).
The worker verifies the owner's identity, DM participant, file uploader and
channel membership before downloading a bounded recording. The configured local
or ElevenLabs transcriber produces question text; **it does not create an
ActivityRecord or treat the colleague's speech as your work evidence**.

The normal grounded-reply engine then prepares an owner review card labelled
as a transcribed voice question. Check transcript accuracy and the answer before
approving the exact reply. Delivery stays in the original personal DM/thread.
Audio failures notify the owner and send no answer to the colleague. To retry a
failed transcription, send a fresh clip. One clip per message, up to 8 MiB and
three minutes, is supported; channel voice mentions and spoken replies are not
part of this flow. Raw audio is processed in memory and is not saved locally.

### Existing workflow safeguards

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

## Learning from personal-DM approvals

New personal-DM approval cards include **Edit & send**. The modal defaults to
**This message only**; its **Send as me** action approves and sends exactly the
edited text through the existing owner-token delivery path. Original text,
correction category, and edit revision are retained privately for audit. Existing
scope, evidence freshness, owner identity, and duplicate-send checks still apply.
No real send is required by the automated tests.

After a successful style edit, **Remember this preference** offers explicit
sentence-style presets (brief/direct, professional sentences, or short bullets).
It never derives facts or permanent rules from arbitrary edited text. Two or more
owner-labelled style edits that shorten messages by at least 25% can suggest a
concise style for that recipient, but never apply it automatically. The heuristic
is a suggestion, not a semantic classifier. Mixed edits should stay message-only.

- Style: explicit saving changes only the recipient's sentence-style field and
  exports a new `soul.md` version. The preference survives later history refreshes.
- Factual correction: the delivered reply's `edit_kind=fact`, original text,
  corrected text, and existing grounding form an evidence-review flag. It does
  not overwrite source evidence or establish a new verified fact. Source repair
  remains a separate owner task.
- Confidentiality: **Review audience policy** opens existing recipient settings
  to restrict allowed projects. Removing text alone grants no permanent policy;
  content-level confidential-topic rules are not inferred.
- Dates/commitments and other one-off edits: apply only to that message.

**Style history / undo** on an edited, delivered style card shows stored versions
and restores the previous style as a new version. Version conflicts reject stale
or replayed saves. History stores style fields only, not message examples; undo
never resurrects deleted examples. Versions predating this migration cannot be
recovered, but the current profile is archived before its first change. Changing
style invalidates older pending replies under the existing policy fingerprint.
These controls currently cover personal-DM reply cards; the browser's standalone
report editor does not automatically learn from edits.
