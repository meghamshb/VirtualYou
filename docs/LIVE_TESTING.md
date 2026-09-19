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
Simulation approval now explicitly says **Approve simulation** and completed
reports say **Delivery simulated**. A failed backend refresh shows an offline
notice and disables review decisions until recovery. These final label and
offline-state changes passed rendered-component tests; they have not yet been
rechecked in the running native app.

## Current demo boundaries

- Electron's local adapter shows `/api/drafts` report approvals. Personal-DM and
  group reply queues remain in Slack; they are not mirrored in Electron.
- Voice capture/transcript review remains in the browser/Slack workflow, not a
  native Electron recording screen.
- The default customer onboarding screen needs a deployed hosted service and
  packaged collector. For this local demo, choose **Developer / preview** to
  reach the saved local connection after launching Electron.
- Jira and Drive credentials are absent locally; their live authentication,
  fetching and end-to-end drafting have not been verified. Group conversation
  delivery and conversation follow-ups were not exercised live in this session.
- Local Slack callbacks depend on the running backend and temporary HTTPS
  tunnel. Restarting the tunnel can change its address, requiring updated Slack
  callback configuration.

## Automated verification

- Root backend/ingestion/hosted suite: **349 passed**.
- Slack workflow suite: **145 passed**; it uses fake Slack clients, separate
  from the live results above.
- An independent agent review reran the 145 Slack and 176 backend tests; these
  overlap the totals above and are not additional distinct tests.
- Desktop build, typecheck, lint, **24 Vitest tests + 2 packaging checks** passed
  for recipient names, refresh, simulation labels and offline decision guards.
  Native UI checks are recorded separately above; no signed package or hosted
  customer deployment was validated.

## Local operator files

The ignored `.virtual-you/slack-live-test/` directory contains `operator.env`,
private backend `data/`, encrypted Electron test state, process IDs and logs.
The one-time credential-entry helper was stopped after configuration.

The original port-8000 browser demo has separate data in
`.virtual-you/local-demo/`; its synthetic voice test is not mixed into the Slack
project's factual work evidence.
