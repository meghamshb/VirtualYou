# Virtual You — Members 2–4 backend

This branch builds on ingestion commit `ab3c90f` from
`phase-1.1,-complete-injestion-pipeline`. It imports Member 1's existing `ActivityRecord`
without modifying the parsers or the ingestion contract.

## Why FastAPI and SQLite

FastAPI gives the team typed request/response validation, an OpenAPI contract,
async calls to models and messaging services, and a lifespan-managed refresh
task. Flask could work, but would need more integration code for these pieces.
SQLite keeps the hackathon deployment to one process and one persistent volume.
Its full-text search supports the first retrieval implementation without a
separate vector database or paid embedding requests.

This is **lexical RAG**: retrieve relevant sanitized activity with SQLite FTS5
(BM25 ranking), supply those records as evidence to the model, validate source
IDs and exact quotes, then present a draft for review. It does not yet perform
embedding-based semantic similarity. A query with no matches returns
`nothing_to_report`; it does not silently substitute unrelated activity.
An empty query selects recent activity, newest first. Session and date filters
are available for daily reports and precise scope.

```text
Member 1: sanitized activity-*.json ──┐
Optional normalized HTTP feed ──────┼─ heartbeat / explicit refresh
Authenticated ActivityRecord POST ─┘              │
                                          SQLite + FTS index
                                                  │
10–20 examples → persona + private soul.md ─┐       │
                                          └─ retrieval + prompt assembly
                                                  │
                                          interchangeable text model
                                                  │
                                      six-section draft + evidence snapshot
                                                  │
                                         review / edit / regenerate
                                                  │
                                    approval of revision + exact destination
                                                  │
                                   explicit delivery → Slack / Discord
```

The core ingestion heartbeat only refreshes evidence. The optional [Slack headless worker](slack_agent/WORKFLOW.md) adds a separate debounced automatic-draft scheduler; it never auto-approves or auto-sends.

The core heartbeat only refreshes evidence. It never generates, approves, edits,
regenerates, or sends a report. Existing drafts keep their original evidence
snapshot; regeneration explicitly retrieves fresh evidence.

## Run locally

Use Python 3.12 on macOS/Linux (the existing ingestion package retains its
Python 3.9 minimum; the backend is verified on 3.12). Run from the repository:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[backend,dev]'
cp .env.example .env
.venv/bin/virtual-you-server seed-demo
.venv/bin/virtual-you-server serve
```

Open `http://127.0.0.1:8000`. In another terminal, run
`.venv/bin/virtual-you-server show-key` and paste the key into the review page.
The browser keeps it in memory only. The API key is generated with private file
permissions in the data directory if no key is configured. Do not share it with
Slack participants or commit it.

`seed-demo` creates explicitly synthetic activity and two example personas. It
does not send anything. It is optional: ingest a real session instead. Repeating
it updates that synthetic session and those example personas.

The default provider is **demo**, an extractive offline template useful for
checking plumbing. It is clearly identified in the UI and draft warnings and
is not a language model. Delivery defaults to **simulation**, with a
`simulated` receipt rather than a false `delivered` claim.

Real text generation:

- Set `VIRTUAL_YOU_LLM_PROVIDER=openai`, `VIRTUAL_YOU_LLM_MODEL` to a model
  available to your API account, and `OPENAI_API_KEY`. The adapter uses the
  Chat Completions JSON-object response format; select a compatible model.
- Or set `VIRTUAL_YOU_LLM_PROVIDER=ollama`, `VIRTUAL_YOU_LLM_MODEL` to an already
  installed model, and optionally `VIRTUAL_YOU_OLLAMA_URL` (default localhost).
  The adapter requests schema-constrained JSON.
- Restart after configuration changes. There is no silent fallback to demo
  after a real model fails. Provider failures leave existing drafts intact.

No model or Slack credentials are included. HTTP adapters are tested with
mock transports; live model quality and live Slack/Discord receipt checks
require your credentials and a designated test destination.

## Member 1 handoff: choose one source of truth per session

**Same machine:** set `VIRTUAL_YOU_DATA_DIR` to the same absolute directory for
both processes, or point `VIRTUAL_YOU_ACTIVITY_DIR` at Member 1's existing
sanitized `activities` directory. Important: ingestion historically defaults
to `~/.virtual-you`, while this backend defaults to `.virtual-you` in its
working directory. Explicit configuration prevents that mismatch.

```bash
export VIRTUAL_YOU_DATA_DIR="$PWD/.virtual-you"
.venv/bin/virtual-you ingest --source claude /path/to/session.jsonl
.venv/bin/virtual-you-server serve
```

The ingestion CLI reads environment variables; it does not load `.env` itself.
The backend CLI loads `.env`. Export the variable in the ingestion terminal.

The heartbeat scans `activity-*.json` every 30 seconds by default. Content
hashes avoid reindexing unchanged records. Changed records replace previous
versions atomically in the index. Records with an older end timestamp cannot
roll a session back. Removed local files are removed from search;
invalid replacements are removed from search and reported. Invalid files do
not stop other files from being indexed. Raw JSONL, Cursor SQLite databases,
recordings, and checkpoints are never read by this backend.

**Separate machines / hosted backend:** send a validated normalized record to
`POST /api/activities`, or configure `VIRTUAL_YOU_ACTIVITY_FEED_URL` to a GET
endpoint returning a JSON array of normalized records. A bearer feed token is
optional; non-local endpoints must use HTTPS. The URL is server configuration,
not a user-controlled fetch parameter. Redirects are disabled.

The feed is an **incremental upsert feed**, not an authoritative deletion
snapshot: omitted sessions remain indexed. Failed feed requests preserve
previous data and mark refresh degraded. For retention/deletion support in a
future multi-user deployment, add explicit tombstones and retention policies.
Do not mix competing local/API/feed writers for the same session ID. Every
wire record must explicitly contain `redacted: true`; downstream redaction is
applied again as defense in depth. No recognizer guarantees removal of every
possible secret, so the source redaction boundary still matters.

Freshness is visible in `/api/status`: refresh start/end, last successful
refresh, counts, and stable error codes. Drafts also include evidence timestamps
and a configurable age warning. A recent heartbeat does not imply recent work.
Local collection stops when the developer's laptop sleeps; hosting the backend
only makes already-synced evidence available while the laptop is offline.

## Member 2: personas

`POST /api/personas` accepts a recipient ID, display name, and 10–20 example
messages. It sanitizes examples before provider use, extracts structured style,
and persists a versioned `PersonaProfile` plus a private derived `soul.md`.
Each profile retains five sanitized examples (verbatim except secret removal).
Separate recipient IDs produce separate profiles.

`soul.md` files and SQLite state are ignored by Git. The structured profile in
SQLite is canonical; Markdown is a local export, available via the `/soul`
endpoint. New exports include a versioned JSON block for standalone prompt
assembly. Editing an export does not update the database; the local assembler
reads only that structured block. See [MEMBER2.md](MEMBER2.md) for the file
format, CLI, synthetic examples, and Member 3 handoff.

The assembler separates style examples from activity evidence and treats both
as untrusted data. Examples are not current work facts. Model-generated drafts
are never automatically delivered. Citation checks reject missing IDs, invented
quotes, and invented links, but cannot prove that a paraphrase logically follows
from its quote. Human review remains necessary. Greetings and sign-offs are
restricted to generic courtesy phrases, so they cannot directly inject an old
project claim outside the cited report. The extractive demo keeps factual report sections identical
across personas; generative providers can vary wording and need review.

## Member 3: drafts, approval, and delivery

The six sections are starting state, approach/rationale, mid-task changes,
result, links, and blockers. Absent evidence produces “Not recorded in the
selected activity.” It does not claim there are no blockers or invent a
rationale for private reasoning omitted by ingestion.

```text
pending ── approve ── approved ── deliver ── delivering ── delivered
   │                      │                       ├── simulated (offline mode)
   ├── reject → rejected   │                       ├── delivery_failed
   └── edit/regenerate ←───┘                       └── delivery_unknown
            │
      next revision, pending
```

- Every mutating action supplies `expected_revision`; stale clients get 409.
- Approval records a hash of revision, message text, and destination.
- Edit or regenerate invalidates previous approval. Regeneration retrieves
  evidence again. Manual edits retain the original report/evidence for reference
  and carry a warning that those citations may no longer support the edited text.
- Approve does not send. Delivery is a separate explicit action.
- An atomic SQLite claim prevents two concurrent clicks from sending twice.
  Repeat requests after success return the saved result without another send.
- Slack receives a stable per-revision `client_msg_id` as additional protection.
- Known rejection (e.g. missing channel membership) is stored as failed and can
  be explicitly retried. A timeout, lost receipt, ambiguous server error, or
  interrupted process is unknown; it is never automatically retried.
- Unknown outcomes require checking the destination and using `/reconcile`.
  Confirming delivery requires a message ID and an audit note. Confirming
  non-delivery restores approved state for an explicit retry. An incorrect
  manual reconciliation can still cause a duplicate: this is not an end-to-end
  exactly-once guarantee from Slack or Discord.
- Audit events and revision history persist across restarts. Terminal delivered,
  simulated, and rejected drafts cannot be edited or redelivered as a new message.

Enable real delivery only after reviewing configuration:

1. Set `VIRTUAL_YOU_LIVE_DELIVERY=true`.
2. For Slack, set `SLACK_BOT_TOKEN` and comma-separated
   `VIRTUAL_YOU_SLACK_CHANNELS` conversation IDs. Install the app with
   `chat:write` and add it to the test channel. A DM requires an appropriate
   existing conversation ID. No arbitrary Slack destination is accepted live.
3. For Discord, set `DISCORD_WEBHOOK_URL` and use target `default`. Message
   content over 2,000 characters is rejected before sending; edit it shorter.
4. Restart, generate a new draft, review its recipient/text, approve, then deliver.

Unfurls and automatic mentions are disabled. The review page shows exact stored
text; Slack transport escapes its special link/mention syntax for literal
rendering. No live messages are sent by the test suite or the heartbeat.

## API contract and Member 4 handoff

Interactive API documentation is at `/docs`; use **Authorize** with the backend
key. All `/api/*` endpoints require bearer authentication. `/healthz`, the static
review shell, OpenAPI schema, and documentation are public and contain no user
records. A key grants owner-level access, including approval. This is a
single-owner prototype, not a multi-tenant permission model.

| Endpoint | Purpose |
|---|---|
| `GET /api/status` | Provider, delivery mode, freshness, available destinations |
| `POST /api/refresh` | Refresh now, without generation or sending |
| `POST /api/activities` | Push a sanitized `ActivityRecord` |
| `POST /api/retrieval/search` | Query, session/date scope, maximum 10 records |
| `POST /api/personas` | Create/update recipient-specific style |
| `GET /api/personas` | List profiles |
| `GET /api/personas/{id}/soul` | Plain-text style export |
| `POST /api/prompts/assemble` | Recipient plus explicitly redacted `ActivityRecord` → prompt; no model call or delivery |
| `POST /api/drafts` | Retrieve evidence and generate a pending report |
| `GET /api/drafts` | Latest 50 drafts |
| `GET /api/drafts/{id}` | Current revision and evidence snapshot |
| `POST /api/drafts/{id}/edit` | Save new pending revision |
| `POST /api/drafts/{id}/regenerate` | Generate new pending revision |
| `POST /api/drafts/{id}/decision` | Approve or reject |
| `POST /api/drafts/{id}/deliver` | Send only an approved revision |
| `POST /api/drafts/{id}/reconcile` | Resolve an ambiguous delivery manually |
| `GET /api/drafts/{id}/audit` | Persistent event history |

Example draft request (not an automatic send):

```json
{
  "recipient_id": "manager",
  "retrieval": {
    "query": "payment validation",
    "session_ids": [],
    "since": "2026-09-19T00:00:00Z",
    "limit": 5
  },
  "destination": {"platform": "slack", "target": "C_TEST_CHANNEL"},
  "question": null
}
```

[Member 4](MEMBER4.md) now adds `/api/voice` upload/review/confirmation and
`/api/assistant/questions` with persistent escalation. Both paths call the same
`Workflow` and `DraftEngine`; voice passes through Member 1 normalization first.
All work answers remain pending until owner approval. The shared gate rechecks
question evidence and persisted Slack audience policy, including HTTP-only
restarts. Realtime voice and spoken replies are not implemented.

Do not expose the owner key to a manager-facing bot/client. Before allowing
untrusted callers, add separate ingestion/draft-only credentials and owner
approval authentication. The present MVP preserves the team's rule that all
manager-facing answers also wait for owner approval; it does not promise
autonomous replies while the owner is asleep.

## Deployment and limits

`Dockerfile.backend` packages the backend with a non-root user. Build it with
`docker build -f Dockerfile.backend -t virtual-you-backend .`. The root
`Dockerfile` remains the phase 1.1 ingestion CLI image. Mount a persistent volume
at `/data`; provide secrets through deployment environment variables. Bind the
service behind HTTPS and appropriate access controls when deployed remotely.
The server uses a filesystem lock to enforce one worker per data directory.
Do not run multiple Uvicorn workers/replicas against this SQLite volume.

For a larger deployment, move state and full-text search to a shared database,
run refresh as a separate scheduled worker, add per-user/project access scopes,
and use a durable job queue. The current heartbeat is in-process and only runs
while the backend is running. It does not monitor Git branches or fetch GitHub
directly; it consumes the agreed ingestion output. GitHub/Jira MCP remains deferred.

## Verification

```bash
.venv/bin/pytest --cov=virtual_you --cov-report=term-missing
.venv/bin/ruff check src/virtual_you/backend src/virtual_you/contracts/reporting.py tests/backend
.venv/bin/ruff format --check src/virtual_you/backend src/virtual_you/contracts/reporting.py tests/backend
```

Tests cover real-shaped ingestion through drafting, two personas, missing
evidence, invalid model citations, redaction, authenticated APIs, periodic and
manual refresh, updated/deleted/corrupt records, remote feed failures, revision
conflicts, approval invalidation, concurrent sends, restart recovery, simulated
delivery, Slack/Discord adapter responses, and ambiguous-delivery reconciliation.
