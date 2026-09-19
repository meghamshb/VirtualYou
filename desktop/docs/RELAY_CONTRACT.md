> Historical UI milestone document. See [Customer deployment](CUSTOMER_DEPLOYMENT.md) for the current implementation and activation requirements.

# Hosted onboarding boundary (proposed, not deployed)

The operator owns provider app registrations, client secrets, signing keys, stable
HTTPS callbacks, tenant isolation and refresh/revocation. The desktop is a public
client and must not contain those secrets. Provider tokens remain server-side;
a device receives only its scoped revocable application credential, stored through
the OS vault. OAuth is performed in the system browser, not an embedded login.

Suggested versioned contract:

- POST /v1/devices/pair: returns device_code, short user_code, expires_at,
  verification_uri and poll interval. Device codes are random, short-lived,
  single-use, and rate-limited; approval binds an authenticated account and device.
- POST /v1/devices/poll: returns pending/expired/denied or a scoped device credential
  only after browser approval. Never log the request body or credential response.
- GET /v1/setup: returns step, device identity and integration status without tokens.
- POST /v1/integrations/{provider}/authorize: returns an allowlisted HTTPS browser
  authorization URL. State, PKCE where applicable, callback and tenant checks live
  on the server. The renderer never constructs provider URLs.
- GET /v1/integrations: provider identity, workspace, granted scopes, last checked,
  connected/expired/needs_scope/revoked and a user-facing corrective action.
- DELETE /v1/integrations/{provider}: revokes/disconnects and prevents queued use.
- POST /v1/activities: accepts versioned explicitly redacted ActivityRecords only,
  project/device binding, payload limits, idempotency key and a content hash.
- Existing draft APIs need a unified adapter for report, personal-DM, group and
  voice queues. Preserve expected revision, recipient policy and evidence checks.

A configured production base URL belongs in packaged operator configuration, not
in a customer-facing wizard. Require HTTPS and a known origin; never accept a URL
from an untrusted message. Do not fetch or execute remote JavaScript. Fail with an
honest service-unavailable state if no deployment exists. The preview reducer is
an explicit local external-boundary simulation, not a relay implementation.

A signed installer must bundle and supervise the Python collector, set up a private
local IPC transport, bind only to loopback, pass device credentials privately and
supply a local-only supported-runtime check. Don't require customers to install
Python or ngrok. Local lifecycle actions must only affect the app-owned process,
not another already-running developer service. Raw logs/audio must not be uploaded
as diagnostics; source excerpts remain sanitized and scoped.
