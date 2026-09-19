# Local integration testing — 2026-09-20

Test checkout: `leo-dev`. This is a local demo, not a deployed customer release.
Credentials, OAuth installations, test audio and runtime databases remain outside
Git. The Slack test app uses the logo exported from the product video.

## Observed live results

- Installed a separate **VirtualYou Leo Test** app in the authorized Test
  workspace. The existing teammate-owned app was left unchanged. Slack verified
  the HTTPS event callback. Both bot and owner `auth.test` checks passed.
- The app Home loaded through real Slack events. Its connection-check and setup
  buttons completed through the signed interactive callback. Git and voice are
  enabled; the selected colleague can access only the `virtualyou` project.
- The colleague had no existing owner-authored DM samples. The reviewed persona
  therefore uses the explicit limited-history default; learned personal style is
  **not** verified by this test.
- A real incoming colleague DM produced a source-grounded reply for owner review.
  The owner approved it in Slack. The persisted state was `sent`, and a read-only
  Slack history check found exactly one matching reply sent as the owner in the
  original DM. A greeting with no work question was held for human follow-up.
- Separately, a work report was approved in **simulation** mode. That report did
  not deliver a message. Report delivery mode and personal-DM approval are
  separate settings in the current implementation.
- ElevenLabs Scribe v2 transcribed a synthetic spoken memo through the running
  backend. It preserved the blocker and “we have not deployed.” Confirmation
  normalized the voice evidence and produced a pending OpenAI `gpt-4o-mini`
  report with no approval or delivery. This did not test physical microphone
  capture in the current session.
- Electron connected to the same port-3000 backend using the native folder
  picker. Its encrypted connection survived an app restart. Real indexed
  projects and report drafts appeared. A fresh report generated from Slack was
  rejected in Electron; the shared backend recorded `reject`, with no receipt
  and no delivery attempt.

## Issues found during testing

One real model request repeatedly added punctuation to an exact source quote;
another returned an uncited report section. Both were blocked before review or
sending. The bounded repair call now receives the rejected report and the
specific failing sections, while retaining the same evidence and strict quote
validation. A subsequent real report succeeded. The report prompt also forbids
inventing causal links between independent statements about blockers and
deployment. Citation checks still do not prove semantic correctness; generated
wording needs human review.

Electron initially showed a hashed recipient ID and required navigating to
Diagnostics to refresh drafts. Local fixes resolve the recipient's display name
and add a **Refresh drafts** control to Approvals.
Simulation approval explicitly says **Approve simulation** and completed reports
say **Delivery simulated**. A failed backend refresh shows an offline notice and
disables review decisions until recovery. The native app now shows the completed
simulation label correctly; offline recovery and decision guards have automated
coverage.

## Current demo boundaries

- Electron's local adapter shows `/api/drafts` report approvals. Personal-DM and
  group reply queues remain in Slack; they are not mirrored in Electron.
- Voice capture/transcript review remains in the browser/Slack workflow, not a
  native Electron recording screen.
- Hosted customer onboarding needs a deployed service and packaged collector.
  Unconfigured developer builds now open the local/preview workspace directly;
  saved local connections reopen automatically. New users must connect their own
  running backend through the native folder picker. See [LOCAL_SETUP.md](LOCAL_SETUP.md).
- Jira and Drive credentials are absent locally; their live authentication,
  fetching and end-to-end drafting have not been verified. Group conversation
  delivery and conversation follow-ups were not exercised live in this session.
- Local Slack callbacks depend on the running backend and temporary HTTPS
  tunnel. Restarting the tunnel can change its address, requiring updated Slack
  callback configuration.

## Automated verification

- Root backend/ingestion/hosted suite: **366 passed** (including portable demo checks).
- Slack workflow suite: **155 passed**; it uses fake Slack clients, separate
  from the live results above.
- An independent agent review reran the 145 Slack and 176 backend tests; these
  overlap the totals above and are not additional distinct tests.
- Desktop build, typecheck, lint, **28 Vitest tests + 2 packaging checks** passed
  for recipient names, refresh, simulation labels and offline decision guards.
  Native UI checks are recorded separately above; no signed package or hosted
  customer deployment was validated.


## Desktop activity and setup follow-up

- Replaced the empty Activity placeholder with a bounded, authenticated feed of
  normalized work records and draft audit events. The running Slack backend
  returned 23 indexed work records and 29 combined events in this check.
- Native Electron displayed the real feed. **Check activity now** entered a
  disabled **Checking…** state and completed with an updated successful-check
  timestamp. Activity collection sends no messages and generates no drafts.
- Approval rows expand independently; **Expand all drafts / Collapse all drafts**
  were exercised natively. Evidence starts closed and individual sources expand
  separately. Source bulk controls are available; bulk-source interaction still
  needs a native pass. The compact collapsed layout was visually inspected.
- Collection health is separate from API connectivity. Valid empty sources,
  malformed input and failed refreshes have distinct states; error details omit
  raw paths and source data. Manual refresh failure updates the persisted status.
- Integration status now reflects configuration presence, not a claimed live
  connection. Native Electron correctly showed Slack configured and GitHub,
  Jira and Drive unconfigured. Local Git collection does not require GitHub OAuth.
- Slack workflow pause is wired through an authenticated endpoint that preserves
  source preferences; stopped/missing workers cannot be controlled. Automated
  tests cover it and the existing delivery guard. No live pause was toggled.
- Current collection is scoped to this repository's Git commits. Claude/Cursor/
  Codex logs are not configured for collection. Voice records enter through the
  existing voice flow; choosing a retrieval source alone never enables collection.
- Saved local connections survive startup failures, retain offline status and
  recover without reauthorizing or retrying failed delivery actions. Diagnostics
  now reads status only; source collection has its own clearly named action.
- A clean macOS source export, fresh Python environment and fresh npm install
  passed backend HTTP and demo draft/approval/simulation checks and desktop build.
  The isolated demo seeds one pending synthetic report and does not use personal
  configuration. This is not proof of setup on a second physical Mac or Intel.
- Slack cards use shared Block Kit templates and clear sender identity. New
  personal-DM candidates include a VirtualYou assistance label before review;
  approved text is unchanged at delivery. Existing drafts remain unchanged.
  155 fake-client tests passed at that checkpoint. A later native Slack inspection
  confirmed the 07:39 labelled owner reply and the matching structured review
  card. The delivered DM still used plain paragraphs; that observation prompted
  the subsequent delivered-message layout work. No colleague message was sent
  by the agent to test that initial design.

## Local operator files

The ignored `.virtual-you/slack-live-test/` directory contains `operator.env`,
private backend `data/`, encrypted Electron test state, process IDs and logs.
The one-time credential-entry helper was stopped after configuration.

The original port-8000 browser demo has separate data in
`.virtual-you/local-demo/`; its synthetic voice test is not mixed into the Slack
project's factual work evidence.

## Desktop logo

The exact transparent VirtualYou logo extracted from the launch video now ships
as the renderer logo, runtime Dock/window icon, and macOS packaging ICNS.
Desktop lint, typecheck and production/Electron build passed. The restarted
native app displayed the new logo and retained its backend connection. The
packaging configuration includes the ICNS; a signed app release was not built.

## Verify real Claude and Codex ingestion

The focused parser/pipeline check passed **25 tests**. That verifies fixtures,
redaction and incremental publication, not a real editor-session acceptance test.
The active local collector was Git only when inspected.

1. Copy the relevant entries from `ingestion-sources.example.json` into your
   private ingestion configuration and set `VIRTUAL_YOU_INGESTION_CONFIG` to its
   absolute path. Use a specific session file or a project-specific directory.
   The project label assigns scope; it does not filter a global log directory by
   workspace. Do not point it at every project's Claude/Codex history.
2. In each coding tool, make one harmless, identifiable change in this repository.
   Confirm the configured file is that tool's actual session log. Restart the
   backend after changing the configuration environment variable.
3. In Electron, choose **Activity → Check activity now**. Confirm successful
   collection and an item with source `claude` or `codex`, project `virtualyou`,
   and the expected change. Zero errors with no matching record is not success.
   Run it again without new work: the unchanged source must not add duplicates.
4. Inspect the normalized local activity record for public explanations and
   redaction; private thinking/analysis must be absent. Use dummy secrets only
   when testing redaction. Raw sessions and normalized personal records stay
   outside Git.
5. Separately enable the source in Slack's retrieval preferences for a recipient
   authorized for this project. Ask a question about the change and inspect the
   pending draft's evidence. No send is needed to verify this step. Selecting a
   source in Slack alone does not configure collection.

## Slack delivered layout and self-test follow-up

- Native Slack inspection confirmed the earlier labelled 07:39 colleague reply
  was still plain paragraphs. The updated personal-DM sender now supplies the
  same branded Block Kit body shown in the review card. Existing messages are
  not rewritten, and the owner's identity and exact reviewed fallback are retained.
- Evidence previews show a count and two short excerpts; **View evidence** opened
  a live modal with the complete quotes. Long evidence has tested pagination.
- Enabled the optional self-test route only for Leo's verified own DM and the
  `virtualyou` project. It operates alongside the existing colleague monitor.
  A real `vy-test:` question generated one pending OpenAI reply. Approval through
  the native Slack card delivered it to that same self-DM, and the native app
  visibly rendered the branded header, assistance label, divider and full body.
- The generated wording still needs human review: this run summarized historical
  Git commits, including a revert, rather than proving current runtime behavior.
  This test establishes routing/presentation/approval, not perfect answer quality.
- Combined Slack suite: **195 passed**, including 23 self-test and 13 timestamp-recovery cases. The focused
  backend activity/integrated-pipeline regression passed **26 tests**. Desktop
  branding lint, typecheck and production/Electron build passed.

Use [SELF_TESTING.md](../slack_agent/SELF_TESTING.md) for the opt-in configuration
and test procedure. Normal self-notes and automated answers are ignored; no
colleague persona is created or altered for self-testing.

### Recovery polling fix found during the self-test

A real Slack history request returned no messages for a seven-decimal Python
timestamp, but returned both test messages when floored to six decimal places.
The shared Slack transport now normalizes only `oldest`/`latest` search bounds;
source event IDs and thread timestamps remain untouched. After restarting with
the fix, the stored polling checkpoint advanced through the delivered reply and
the self-test database still contained exactly one sent reply, with no extra
queued or pending reply. Read-only Slack history confirmed one delivered message
with matching approved wording (Slack normalizes fallback whitespace) and matching
Block Kit content (excluding Slack-added block IDs/default emoji flags).
