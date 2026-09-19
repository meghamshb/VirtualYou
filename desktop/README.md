# VirtualYou desktop UI

An Electron + React + TypeScript interface on `feat/app`, based on committed
`develop` at 092151c. This is a UI milestone, not the completed hosted onboarding
service. The existing Python, Slack, MCP and delivery code is unchanged.

## Try it

Developer commands (customers will eventually receive a signed installer):

```sh
cd desktop
npm ci
npm run desktop
```

Browser preview: `npm run dev`, then http://127.0.0.1:5178. Browser mode is always
sample-only and cannot access local credentials. The welcome dialog and header
make this explicit. Progress persists in browser localStorage (sample data only)
or Electron's application data folder. No test requires a real Slack/OpenAI account.

The preview supports connecting four example integrations, selecting projects,
back/next/resume, a sample evidence-backed draft, approval/rejection, pause/resume,
activity and copyable diagnostics. It makes no external service calls.

## Existing local backend

In the desktop app: Settings → Developer / Advanced → enter the local port →
Choose data folder. Select the existing private directory containing `admin.key`.
No key needs to be copied. The process must already be running. The desktop only
connects to 127.0.0.1, retains the credential in Electron main and stores encrypted
connection data using Electron safeStorage (macOS Keychain protects the encryption
key). It refuses plaintext fallback. Disconnect removes its encrypted credential.
Existing backend `.env` and storage are not moved or rewritten.

The interface reads `/api/status` and `/api/drafts`, refreshes via `/api/refresh`,
and uses existing `/decision` then `/deliver` endpoints after confirmation. It
shows report drafts exposed by those endpoints, not every Slack DM/group queue.
It does not bypass server checks or retry uncertain sends. Live delivery identity
and simulation settings are controlled by the backend. Existing project policies
and integration configuration stay in Slack; the UI explicitly says so.

Production hosted OAuth, device pairing, collector lifecycle management, complete
DM/group approval APIs, account/source selection and a bundled Python runtime are
not implemented here. Integration rows in local mode do not infer an authorization
from evidence. They explain that authorization remains managed by the deployment.
See [the refined brief](docs/BRIEF.md) and [relay contract](docs/RELAY_CONTRACT.md).

## Package and test

```sh
npm run lint
npm run typecheck
npm test
npm run build
npm run package:mac
```

`release/` contains the unpacked macOS app. Signing uses electron-builder's normal
operator configuration and requires Developer ID credentials; no credential goes
in package.json. This UI app needs no Python runtime in preview. Production
collector distribution is a separate milestone.

Security: sandboxed renderer, isolated preload, no nodeIntegration, restrictive
CSP/navigation, no remote content, narrow validated IPC and exact main-frame
checks. The browser development server alone relaxes inline scripts for React
refresh; packaged CSP keeps `script-src 'self'`. No telemetry uploads or auto-update
network calls are configured. Diagnostics exclude identities, paths and messages.

Native smoke test: `npm run test:electron`. It uses a temporary application data
folder, exercises the real preload/IPC, restarts Electron and verifies that preview
progress survives. It never attaches to the running Slack service.

For an unsigned local package, use
`CSC_IDENTITY_AUTO_DISCOVERY=false npm run package:mac`. This avoids automatically
selecting a developer certificate. Signing/notarization is a release task.
