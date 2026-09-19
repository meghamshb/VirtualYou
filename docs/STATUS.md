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

## Remaining live acceptance and limitations

Configure a valid local ElevenLabs key with Speech to Text permission and check
account allowance before selecting it; cloud request/error shapes are mocked,
not evidence of a live ElevenLabs transcription. Reauthorize the Slack app's
new `files:read`, then test an actual owner memo and recipient mention through
review to one approved destination. No real Slack/Discord messages were sent
in this implementation run. Persona/factual quality still needs the selected
live text model and human acceptance from [MEMBER2_ACCEPTANCE.md](../MEMBER2_ACCEPTANCE.md).

The standing worker/startup service is reused, not deployed or proven available
24/7. The Mac/host must stay awake and online. Docker builds were not run.
Discord's shared outbound adapter remains available; Discord inbound events,
spoken responses/realtime voice, an in-page microphone recorder, video and slides
are not part of this change. Voice recordings are not persisted by this app;
Slack/cloud retention is controlled by those providers. No raw media, credentials,
private persona samples or concept-video files are included in the PR.
