> Historical allocation audit from the original Member 4 branch. For the merged
> feature set and current verification, read [LEO_INTEGRATION.md](LEO_INTEGRATION.md).

# Leo's allocation audit

Checked against the unchanged [TEAM_DELIVERABLES.md](../TEAM_DELIVERABLES.md),
the local machine transcript of the teammate's September 19 voice note, current
source, and the checks recorded in [STATUS.md](STATUS.md).

The audio assigns Leo Member 4 as well as presentation work. Leo subsequently
asked to pause video/slides, chose voice memo → draft first, and selected
ElevenLabs for transcription. Those direct choices govern this implementation.
The reference documents are requirements to compare against, not independent
permission to send messages, deploy services, or change the agreed PR base.

## Findings requiring attention

1. **Recording UX was incomplete.** Upload/STT existed, but the plan explicitly
   gives Member 4 ownership of recording. This update adds browser Record → Stop
   → playback preview → explicit transcription alongside file upload.
2. **Live Slack acceptance is still open.** Event handlers and fake-client tests
   exist; an actual recipient mention and owner-uploaded Slack memo have not been
   demonstrated through approval to one live recipient message. The app needs
   installation/reauthorization, `files:read`, event subscriptions, an authorized
   recipient/project, and a running Slack worker. The server at port 8000 is the
   review backend; opening that page alone does not start the Slack event worker.
3. **Continuous hosting is still open.** The inherited persistent worker and
   startup support are implemented. No always-on deployment or laptop-asleep
   uptime test has been completed. A running local review server is not this proof.
4. **Discord inbound support is missing.** The shared outbound adapter can accept
   approved drafts, but there is no Discord gateway/listener for manager questions
   or voice attachments. This is a secondary-platform gap in the full written
   allocation, not a completed feature or an explicitly agreed scope deletion.
5. **Full autonomous offline answering is not met.** Work answers queue for owner
   approval. Escalations show the explicit unknown response to the owner, without
   sending it automatically to the manager. The original always-available-reply
   ambition conflicts with its approval requirement; the implementation preserves
   the gate. Slack work answers currently go to the approved recipient's bot DM,
   even when a question originated in a channel, not back into its original thread.
6. **Quality acceptance remains for voice and persona.** Synthetic STT checks and
   deterministic drafts do not prove accuracy for Leo's accent/noisy recordings,
   convincing personal writing style, or semantic truth with the chosen live model.

## Every Member 4 task

“Implemented” below means the source path exists and has local evidence. It does
not imply live Slack acceptance, deployment, or general natural-language coverage.

| Task from allocation | Implementation / evidence | Outstanding work |
| --- | --- | --- |
| 1. Always-on bot process | Inherited `HeadlessRuntime`, coordinator job loop, OAuth entry point and startup instructions in [WORKFLOW.md](../slack_agent/WORKFLOW.md) | Host it continuously; verify restart, reachability and uptime |
| 2. Manager mentions/questions | Slack `app_mention`, bot DM, signed user DM and shortcut route to `Member4.receive_member4` / `queue_question`; event/poll dedup tests | Live Slack mention → reviewed answer; Discord incoming events absent |
| 3. Latest record + correct recipient profile | Scoped shared retrieval, recipient profile ID, allowed projects/sources, freshness and audience-policy checks | Rehearse with the team's real normalized record and recipient |
| 4. Reuse Member 3 engine | `AssistantService.ask` uses shared `Workflow.create` → `DraftEngine`; voice confirmation uses the same path | Integrated live-model rehearsal |
| 5. Evidence check before answering | Missing/future/stale evidence escalates; shared grounding checks; evidence/policy rechecked at approval/delivery | Human factual review remains necessary; quote matches alone do not prove claims |
| 6. Four routine question types | Completed work, changes, blockers, current status; all four have tests producing cited pending drafts | Phrase matching is conservative English, not arbitrary conversation; realistic wording may escalate |
| 7. Unsupported questions escalate | Scope decisions, commitments, deadlines, opinions, unknown/compound requests; tests cover all four named categories | Live inbox demonstration |
| 8. Flag escalations for user | SQLite persistence; Slack Home and web inbox; owner-only resolution with no send | Confirm visibility in the installed Slack app |
| 9. Accept voice memo | Web recording and file upload; Slack owner-audio upload handler | Physical-microphone/device acceptance and actual Slack private-file download |
| 10. STT → Member 1 normalizer | Local Whisper or ElevenLabs Scribe → redacted transcript → user correction → `IngestionService.ingest_transcript` → user-reported activity → pending draft | Representative accent/noise checks; current account allowance not queried |
| 11. GPT-live if time permits | Explicit stretch task; ordinary speech-to-text reuses shared text drafting, as chosen by Leo | Realtime voice and spoken answers remain optional, unimplemented |

### Measurable deliverables and definition of done

- **Slack mention response:** implemented routing and review, but the required live
  manager-to-approved-answer demonstration is pending.
- **Four supported / four unsupported types:** automated cases pass. Inputs must
  match the documented supported phrases or they escalate conservatively.
- **Evidence-backed answers / explicit unknowns:** local tests cover citations,
  missing evidence, stale/future records, source/project scope and changed evidence.
  Unknowns persist in the owner's queue; they are not autonomous manager replies.
- **Stored and visible escalations:** backend restart, web UI and Slack Home tests
  pass; Slack visibility still needs its real-workspace check.
- **One transcribed memo / shared normalization:** real synthetic audio was
  transcribed with both local Whisper and live ElevenLabs. Confirmation and browser
  flows exercise normalization and pending-draft generation. This is not proof of
  accent/noise robustness or of real Slack file access.
- **At least eight tests:** 31 backend Member 4 cases and 18 Slack Member 4 cases
  pass. Total regression suites include other members' work; extra test count does
  not close the live acceptance gaps.
- **No duplicated generation:** question and voice services call the shared
  workflow/engine. There is no separate Member 4 model-generation implementation.

**Member 4's full definition of done is not yet satisfied.** The core local paths
exist; the outstanding platform, deployment and end-to-end acceptance items above
must remain visible in the team handoff.

## Earlier Member 2 allocation

Leo's earlier Member 2 work remains in the parent branch/PR. The audio adds Member 4;
it does not establish that persona acceptance has already finished.

| Member 2 requirement | Current evidence | Remaining acceptance |
| --- | --- | --- |
| Accept 10–20 messages; analyze all listed style traits | Form, CLI/API, provider adapters and count/trait tests | Consented representative samples with the chosen real model |
| Local `soul.md`, 3–5 verbatim examples, one profile per recipient | Five sanitized examples; private export, round-trip, version and recipient-isolation checks | Human review of example representativeness |
| Assemble complete prompt; separate style from facts | Shared assembler plus CLI/API; evidence and persona-data separation; redaction/injection tests | Team's selected real-record handoff |
| Same record, visibly different tone, identical facts | Offline paired runner checks evidence/facts and detects unchanged report bodies | **Not complete:** demo provider changes greetings/sign-offs, not substantive body style; run selected live model and review both outputs |
| Profiles excluded from Git; at least five tests | Private storage/ignore checks; dedicated persona and evaluation suites | Preserve private storage when onboarding real recipients |

Use [MEMBER2_ACCEPTANCE.md](../MEMBER2_ACCEPTANCE.md) for the paired live-model
evaluation. The current local review backend uses demo text generation, so it
cannot close this quality criterion by itself.

## Ownership and next acceptance order

1. Leo: finish physical microphone and representative recording checks against the
   implemented recorder, then verify transcript correction and a pending draft.
2. Leo with the Slack installer/Member 3: authorize events/files, configure the
   intended recipient and project, then rehearse mention → owner approval → exactly
   one message to the displayed destination. Repeat with a Slack voice memo.
3. Leo with the hosting/integration owner: select/run the persistent worker on the
   agreed host and verify it remains available when the user's laptop is asleep.
4. Leo with Member 3: run paired persona/model acceptance on the selected real
   normalized session. Member 1 owns raw-log collection and normalization; Member 3
   owns the shared draft engine, approval enforcement and delivery adapters.
5. Team: implement Discord incoming events for the full two-platform allocation,
   or explicitly agree that the submitted demo covers Slack only and disclose it.
6. Integrator: reconcile this phase 1.1 / Member 2 PR with newer `develop` separately.
   Leo chose the current base; this audit does not change it or overwrite other work.

Video/slides remain paused by Leo. The teammate assigned MCP work to himself and
Tanish in the audio; it is not added to Leo's Member 4 implementation here. No
teammate was contacted and no live recipient message was sent by this audit.
