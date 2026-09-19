> Historical UI milestone document. See [Customer deployment](CUSTOMER_DEPLOYMENT.md) for the current implementation and activation requirements.

# Refined implementation prompt: VirtualYou desktop UI

Build the first desktop interface for VirtualYou on `feat/app`, branched from the
committed `develop` branch. Preserve existing backend and Slack behavior; don't
modify another active checkout or copy its unfinished changes.

## The problem

A customer must not manage `.env`, developer secrets, manifests, ngrok, Python, or
callback URLs. The eventual product should pair a desktop collector with a
centrally operated service and let users connect their accounts in a browser.
OAuth does not remove the operator's need to register apps, host callbacks, secure
provider credentials and obtain any required provider approval.

## This milestone: the desktop interface

Use Electron, React and TypeScript. Build a calm, accessible workspace with Setup,
Integrations, Projects, Approvals, Activity, Diagnostics and Settings. Start with a
three-step wizard: connect tools, choose projects, review defaults. Explain
permissions in plain language. Make cancellation and errors recoverable, preserve
progress across restarts, and clearly label every preview/simulated action.

Represent Slack, GitHub, Jira and Google Drive. Do not claim an account is connected
until a real integration confirms it. For this UI milestone, a clearly labelled
preview may simulate external-service boundaries so the complete interaction can
be reviewed without credentials. Never substitute mocks inside existing working
Python integrations.

Provide an explicitly labelled Developer / Advanced connection to an existing
local backend. Use narrow typed IPC; keep its access credential in the Electron
main process, protected by the OS. Do not put keys in renderer state, URLs,
localStorage, diagnostics or source control. Display real backend health and
existing report drafts; delegate approval/delivery to existing backend endpoints.
Don't start, stop, pause or reconfigure an existing deployment implicitly.

Approval is the default product experience. Preview approvals never send. For a
real backend, show the actual destination and require explicit confirmation;
keep server-side policy, evidence freshness and uncertain-delivery behavior.
Don't pretend a desktop toggle changes Slack permissions or automatic-send rules.

Use semantic controls, keyboard focus, readable typography, responsive layouts and
reduced-motion support. Restrict renderer navigation, disable Node integration,
enable context isolation and sandboxing, validate IPC payloads and sender frames.
Prepare macOS packaging; do not claim a signed distributable without verifying it.

## Next milestone: production onboarding

Keep this separate from the UI delivery:
- Centrally managed Slack app and hosted, multi-user OAuth/pairing service.
- Short-lived single-use device codes and account/device revocation.
- OAuth and refresh support for GitHub, Jira and Drive using existing adapters.
- Bundled Python collector with managed lifecycle and native project selection.
- Encrypted local credentials and managed server-side provider secrets.
- Sanitized ActivityRecords only across the local/hosted boundary. Raw editor
  history, audio and repository content must not be uploaded by the collector.
- Project/audience configuration backed by real server authorization.
- Automatic-reply controls that apply atomically to queued work.
- Signed/notarized installer, runtime distribution, updates and operator deployment.

Document the relay contract and missing deployment prerequisites. Never ask end
users to supply Slack app secrets or silently substitute a fake production service.

## Verification and handoff

Test wizard transitions, cancellation, persistence, stale/duplicate approval,
secret-free diagnostics, IPC validation, OS-encrypted credential storage through
an injectable fake, and the backend boundary. Exercise the complete preview in a
browser and actual Electron. Run type checks, lint and relevant repository tests.
Provide the refined brief, runnable UI, screenshots, branch/commit and clear
remaining production work. Don't present preview connections as live OAuth.
