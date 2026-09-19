# Customer onboarding implementation and operator activation

The `feat/app` branch now contains real customer OAuth, device pairing, an isolated
hosted backend, native account/resource selection and a bundled local collector.
The original CLI and developer preview remain available. No normal customer step
asks for a key, a developer app, a manifest, ngrok or a terminal.

## What was found in the repository

There is an existing single-owner Dockerfile and local/ngrok documentation. No
configured hosting provider, owned production domain, hosted OAuth service or
release signing setup was found in tracked configuration. The Jira/Drive adapters
are also on a separate remote feature branch; the existing local integrations
have not been replaced. The new hosted readers use account-scoped OAuth and only
fetch selected, bounded resource metadata.

**The repository is not a deployed service.** A central operator still needs to
activate the service below once. Until its HTTPS URL is packaged, the app explicitly
shows service unavailable, never fake successful account connections.

## One-time operator activation

1. Deploy `Dockerfile.customer` to a host with a persistent disk and HTTPS. The
   companion `deploy/customer.compose.yaml` supports a private Docker host behind
   an HTTPS reverse proxy. Run one worker/replica for this SQLite MVP. Set the
   external origin as `VY_PUBLIC_URL` and mount persistent storage at `/data`.
2. Configure a persistent `VY_ENCRYPTION_KEY` in the host's secret manager. Generate
   it with `Fernet.generate_key()` from Python cryptography. Keep a secure backup;
   changing it without migration makes saved credentials unreadable.
3. Register/own the shared provider apps and put their client ID/secret in the host
   secret manager. Register exactly these URLs using your actual origin:

   | Provider | Callback path | Operator configuration |
   | --- | --- | --- |
   | Slack | `/oauth/slack/callback` | SLACK_CLIENT_ID, SLACK_CLIENT_SECRET, SLACK_SIGNING_SECRET |
   | GitHub | `/oauth/github/callback` | GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET |
   | Atlassian Jira | `/oauth/jira/callback` | ATLASSIAN_CLIENT_ID, ATLASSIAN_CLIENT_SECRET |
   | Google Drive | `/oauth/drive/callback` | GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET |

   Slack Events and Interactivity use `/slack/events`. Generate the operator
   manifest with `python -m virtual_you.hosted.manifest https://YOUR_ORIGIN`.
   Enable app distribution and obtain workspace/provider approvals required for
   your customers. Google consent-screen testing restrictions do not constitute
   production distribution. Enable the Drive API and Atlassian 3LO APIs.
4. Configure the operator's `OPENAI_API_KEY`. The hosted service uses GPT-4o mini;
   customers never need a model API key. API billing belongs to the operator.
5. Verify `/healthz` reports the expected public origin, configured providers and
   model readiness. Provider callback registration is external; the code derives
   one canonical URL but cannot inspect provider consoles to prove a match.
6. Set the public `baseUrl` in `desktop/service.json` before building. This file
   contains no secret. Build the collector and macOS app, then sign and notarize
   the release with your Developer ID. Distribute that installer to customers.

```sh
# Operator/release engineer only, from the repository root:
python3 -m venv .build-venv
.build-venv/bin/pip install -e '.[collector]' pyinstaller
cd desktop
npm ci
VY_BUILD_PYTHON=../.build-venv/bin/python npm run package:customer
```

The build environment must use a supported Python/SQLite version. Builds are
native to the build machine architecture; produce separate arm64/x64 releases.
The current local build is macOS arm64 and not Developer ID signed/notarized.

## Customer path

- Open the app; its private collector/runtime is bundled, with no listening port.
- Connect Slack in the system browser. Enter the short code displayed in the app,
  select the intended workspace and approve Slack's requested permissions.
- Connect optional GitHub, Jira and Google Drive accounts. Choose a Jira site,
  repositories/folders, local projects and allowed colleagues. Selection grants
  sharing only for those projects. Resources are not inferred from credentials.
- Collect sanitized summaries, then ask a setup question. Review text and evidence,
  explicitly approve a real message to your **own Slack app conversation**, and
  confirm the delivery receipt. Finish setup requires that successful test.
- Review each colleague's separate inferred style before enabling their drafts.
  Personal-DM replies use the existing owner-token delivery workflow; approval
  cards remain available in Slack. Automatic sending remains off.
- The desktop refreshes local evidence each minute while running; the server
  refreshes selected remote evidence every two minutes. Slack events are accepted
  durably and dispatched separately, outside the three-second acknowledgement.
- Pause, reconnect and disconnect are available. Disconnecting a provider stops
  use and removes its indexed evidence. Provider-side grants can also be removed
  from that provider's security settings. Closing the app stops local collection;
  the hosted listener can still use previously authorized evidence.

## Boundaries and security

- Separate backend/coordinator/data directory for each verified Slack owner/team.
- Provider tokens and pending grants are encrypted at rest. Desktop credential
  encryption is protected by macOS Keychain; plaintext fallback is refused.
- Refresh tokens rotate serially; Slack workspace bot tokens are shared centrally
  while user credentials remain owner-specific.
- OAuth state expires, is single use and is bound to an HttpOnly browser cookie.
  GitHub/Google use PKCE. Device credentials expire and are individually revocable.
- The renderer has only validated typed IPC. The executable path is fixed by the
  package, not supplied by the renderer. No shell command is executed by renderer.
- Local collector reads selected Git commit metadata and exact matching
  Claude/Cursor/Codex project history. Raw prompts, patches, tool I/O and absolute
  source paths are not uploaded. Selected summaries are redacted again server-side.
- Existing approval revision, evidence snapshot and uncertain-delivery checks stay
  in charge. No retry of an uncertain send. Only explicit approval sends.

## MVP operational limits

Single-process persistent-disk deployment, bounded resource lists (first 100
GitHub repositories/Drive folders, 50 Jira projects, 200 Slack users), and metadata
rather than full Drive document contents. Remote readers collect selected latest
commits/issues/folder entries; they are not unrestricted account crawlers.
The desktop displays backend report drafts; DM/group approval cards use Slack.
Large production fleets still need shared transactional storage, distributed
workers, rate-limit coordination, tenant quotas and operational monitoring.

## Verification

Tests cover real OAuth adapter calls with external HTTP fixtures, state/cookie and
workspace failures, missing scopes, expiry, token rotation, cross-device auth,
raw-field rejection, and a full actual backend evidence → draft → approval →
delivery flow. No real account was authorized or Slack message sent during tests.
The Docker daemon was unavailable locally, so container startup is not verified.
The native collector and Electron executable are tested separately.

Official references used:
- https://docs.slack.dev/authentication/installing-with-oauth/
- https://docs.slack.dev/authentication/using-token-rotation/
- https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
- https://developer.atlassian.com/cloud/jira/software/oauth-2-3lo-apps/
- https://developers.google.com/identity/protocols/oauth2/web-server
