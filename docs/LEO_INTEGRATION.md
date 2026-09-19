# Leo integration audit — 2026-09-20

PR #4 merged the original `leo-dev` integration into `develop` at `33d5301`.
`leo-dev` now starts from that merged commit. Conversation-context follow-up fixes
are committed and reviewed on `leo-dev` only; this follow-up does not commit, push
or merge to `develop`. Do not merge the older PRs into their old phase branches
as an additional integration step.

During the follow-up, remote `develop` advanced to `40915b0`, a teammate's revert
of the desktop/customer-hosting merge. The user subsequently authorized restoring
Electron in the existing `leo-dev` PR. That revert is reconciled on `leo-dev`,
retaining the desktop app, customer/collector support, deployment templates and
tests. GitHub/Jira/Drive source types and Slack source allowlists are also retained;
removing them would regress the previously integrated connectors. This restores
existing code and does not deploy a hosted service or establish customer readiness.

The PR also includes report repair feedback and desktop review fixes found during
live testing. Current results are **366 root tests, 155 Slack workflow tests,
28 desktop tests and 2 packaging checks passed**. See
[LIVE_TESTING.md](LIVE_TESTING.md) for the real Slack DM, provider and native
Electron results, and the remaining acceptance gaps. `develop` and `main` are
not modified by this follow-up; the user will confirm before any move to `main`.

## Conversation-context follow-up

`conv_context` remains at `24a1df7`, already an ancestor of `leo-dev` through merge
`7ec02f1`. The original branch's eight changed files were compared with the
integrated versions. No additional source-branch commits needed merging.

The audit found that the original clarification suggestion had been replaced by
a generic escalation. It also found that the current question and newer queued
messages could contaminate topic resolution. The follow-up fixes those behaviors
while retaining the current assistant, group replies, formatting and owner checks.

| Feature | Preserved behavior and verification |
| --- | --- |
| Pronoun and generic-topic references | Resolve “it” and “the connector” against earlier turns in the same conversation; current and later messages are excluded, including out-of-order polling |
| Ambiguity | Offer a deterministic choice among 2–8 topics in a private approval card; no model call or colleague send before approval. Larger/invalid choices and judgment requests escalate |
| Short-lived topic graph | Turns, nodes and edges expire after three days; replaying a retained turn does not refresh retention |
| Privacy and isolation | Redaction precedes storage; model history is limited to eight turns of 750 characters. DMs and threads have separate context, including after restart |
| Delivery memory | Retain only cited evidence identifiers and hashes after a successful send; unchanged and changed work get their respective delivery annotations in the next prompt |
| Grounding and approval | History remains reference context, never work evidence. Exact clarification text is validated separately; factual answers keep scope, citation and freshness guards |
| Failure and edits | Rejection/uncertain sends create no delivery baseline. Human edits do not inherit draft citations. Delivery state and context commit together; restart does not replay a send after a persistence failure |

Verification: **348 root tests + 145 Slack workflow tests passed**, plus scoped
Ruff and `git diff --check`. The new regressions reproduced the clarification and
ordering failures before the fixes. Slack delivery was exercised with fake clients,
not a real workspace. No external messages were sent. Desktop source is unchanged;
the desktop and provider checks below belong to the earlier integration run.

## Branch inventory

| Source | Status at original audit (before PR #4) | Integration |
| --- | --- | --- |
| `feat/app` (`6de7407`) | Already in develop | Preserved Electron UI, customer onboarding/readiness checks and local backend adapter |
| `feature/jira-drive-mcp` (`0e58d77`) | Not merged; no PR | Merged named-ticket Jira observations, work-state summaries, heartbeat refresh and bounded Drive folder metadata |
| `conv_context` (`24a1df7`) | Not merged; no PR | Merged short-lived DM/thread context and successful-delivery evidence references |
| PR #2 / `feat/member-2-persona-phase-1.1` (`d2ec671`) | Open against old phase 1.1 branch | Merged portable profiles, prompt handoff API/CLI, synthetic paired evaluation and safe style extraction into the current persona service |
| PR #3 / `codex/member-4-voice-assistant` (`4f85fc9`) | Open against PR #2's branch; voice features selectively ported earlier | Reconciled ancestry, retained newer voice/escalation/policy code, restored opt-in local audio diagnostics |
| Earlier ingestion, phase 1.1, phase 2 Slack branches | Already in develop | Preserved |
| `feat/member-2-persona` / closed PR #1 | Ancestor of PR #2 | Included through PR #2; no separate merge needed |

PRs #2 and #3 retain their original GitHub bases and are not independently merged
or closed by this task. The integration PR contains their ancestry and compatible
features. The local video checkpoint (`823272c` on `chore/workspace-setup`) is
separate and is not part of this application PR.

## Integration decisions

- Jira/Drive remain opt-in and read-only. Jira looks up up to three explicitly
  mentioned issue keys; Drive lists at most five metadata entries from one chosen
  folder and does not read document contents. These are REST adapters under the
  `mcp` package, not a new generic MCP client or completed customer OAuth UI.
- Preserve `develop`'s recent-sort behavior, evidence snapshots, recipient policy
  guards and approval checks. Reject unredacted feed records before Jira lookup.
- Record DM context in the existing database transaction, avoiding nested SQLite
  writer locks. Keep channel/thread history separate, expire it after three days,
  and record a delivery baseline only after a successful send. Human-edited replies
  do not inherit citations to unchanged draft text.
- Feed conversation context through the current assistant escalation service;
  preserve group context, citation labels, source scoping, Slack formatting,
  style learning and owner-token delivery. Ambiguous factual references produce
  owner-approved clarification questions; judgment requests still escalate.
- Use one shared persona service and report prompt assembler. Preserve formal
  fallback for sparse histories, empty profiles, history/undo, and learned sentence
  preferences while adding lossless portable `soul.md` exports and standalone tools.
- Inspect logical prompt JSON before escaping so already-redacted source-code
  assignments are accepted. Report citation failures get at most one repair using
  the same evidence; a second failure still creates no draft or delivery.
- Keep the current unrestricted factual-question RAG assistant rather than reviving
  the old four-phrase classifier. Retain `test_voice.py`, `test_assistant.py` and
  current Slack policy tests in place of the superseded old Member 4 test module.
- Restore `virtual-you-server serve --voice-test` only as explicit loopback
  diagnostics. Ordinary review APIs still require the owner key. Diagnostic POSTs
  check peer/Host/Origin, use the current locks and redact all configured service
  credentials. This mode stores no note, activity or draft and sends no messages.

## Earlier integration verification (PR #4)

- Backend/ingestion/hosted suite: **346 passed** (`python -m pytest tests`).
- Slack workflow suite: **137 passed** (`PYTHONPATH=src:slack_agent python -m pytest slack_agent/tests`).
- Desktop: ESLint, TypeScript, 18 Vitest tests, 2 release-preflight tests, Vite and
  Electron production builds passed. Desktop source is unchanged by this integration.
- Member 2 offline demo: identical evidence/factual report across two synthetic
  recipients, separate presentation, no outbound messages. Paired evaluation:
  three pairs; correctly reports `needs_live_model` and human acceptance pending.
- Live GPT-4o mini: the assistant produced a cited pending answer from real local
  repository Git evidence. A six-section report also succeeded on explicitly
  synthetic seeded activity. An earlier invalid citation was rejected without
  creating a draft; bounded report repair was subsequently added and tested.
- Native Electron UI: connected to the real loopback backend via the private
  data-folder picker, displayed the OpenAI report and sources, confirmed approval,
  and showed no message sent. The API confirms `status=simulated` and a persisted
  simulated delivery receipt. This is not a live Slack send.
- Live ElevenLabs Scribe v2 transcription was verified during local setup with
  an eight-second existing synthetic narration clip (HTTP 201, review required).
  No physical microphone capture was performed in this integration run.
- Backend remains on port 8000 with live delivery disabled. Credentials, runtime
  data, recordings and local video are excluded from the commit.

Python suites report an upstream Starlette/AnyIO deprecation warning. Provider
validation is separate from external delivery. No Slack, Discord, Jira or Drive
writes were performed. Hosted deployment and signed packaging were not run.

## Run the local demo

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[backend,collector,dev,voice-cloud,hosted]'
uv pip install --python .venv/bin/python -e ./slack_agent
# Configure private .env from .env.example; do not commit it.
.venv/bin/virtual-you-server seed-demo
.venv/bin/virtual-you-server serve --host 127.0.0.1 --port 8000
```

`seed-demo` is for an isolated demonstration store and creates synthetic activities
and personas. Skip it against an existing real workspace. Use an explicit
`VIRTUAL_YOU_INGESTION_CONFIG` to select project sources; do not ingest global
personal session history for a demo.

In another terminal:

```sh
cd desktop
npm ci
npm run desktop
```

Choose **Developer / preview → Settings → Developer / Advanced**, enter `8000`,
then choose the private data folder containing `admin.key`. No key is copied into
the renderer. On this machine the isolated folder is
`.virtual-you/local-demo`. Use **Refresh drafts** in Approvals to refresh draft state.
The browser-only desktop preview is sample data; it is not the local adapter.
The backend review and voice interface is at `http://127.0.0.1:8000/review`.

Keep `VIRTUAL_YOU_LIVE_DELIVERY=false` for rehearsal. To test only ElevenLabs,
stop that backend and restart it with `--voice-test`; the isolated tester is then
at `/`, and authenticated review stays at `/review`.

## External work still required

- Slack installation/user OAuth and one owner-approved personal-DM reply passed
  in the separate Test workspace. Report delivery remains simulated; live group
  and conversation-follow-up acceptance remain outstanding. See the live-test log.
- Live Jira needs `VIRTUAL_YOU_MCP_JIRA=true`, `JIRA_BASE_URL`, `JIRA_EMAIL` and
  `JIRA_API_TOKEN`. Drive needs `VIRTUAL_YOU_MCP_DRIVE=true`, a chosen
  `VIRTUAL_YOU_DRIVE_FOLDER_ID` and `GOOGLE_ACCESS_TOKEN`. These adapters were
  tested with mocked services; they are not connected in this local demo.
- Customer onboarding still needs the hosted HTTPS service, OAuth operator setup,
  bundled collector and signed release. The local developer adapter does not prove
  the distributed customer flow.
- Paired persona quality still needs consented representative messages and human
  review. Synthetic extraction/evaluation does not establish real style fidelity.
- Video scenes and files are unchanged. Capture real product UI only after the
  intended demonstration path is accepted.
