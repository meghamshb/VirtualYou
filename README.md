> **Pending integration:** `leo-dev` combines the remaining Jira/Drive, conversation-context,
> Member 2 and Member 4 branches. See [integration audit and demo status](docs/LEO_INTEGRATION.md).
> The shared `develop` branch is unchanged until the integration PR is approved and merged.

> **Integrated development branch:** see [the complete pipeline and branch comparison](docs/DEVELOP_INTEGRATION.md).
> `develop` connects project ingestion, scoped RAG, GPT-4o mini, recipient style, and approved replies in the owner's original Slack DM. Configure background sources with `VIRTUAL_YOU_INGESTION_CONFIG`; raw collection is separate from each message request.

# Virtual You

Members 2–3's FastAPI backend is now available: recipient personas, searchable
activity with a refresh heartbeat, drafting, approval, Slack/Discord delivery,
and a minimal review page. See **[BACKEND.md](BACKEND.md)** for setup, API
contracts, Member 1/4 handoffs, deployment, and known limits.

Member 2's local persona tools, portable `soul.md`, prompt API, and two-recipient
acceptance demo are documented in **[MEMBER2.md](MEMBER2.md)**.
Paired live-model checks and their current evidence are in
**[MEMBER2_ACCEPTANCE.md](MEMBER2_ACCEPTANCE.md)**.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[backend,dev]'
cp .env.example .env
.venv/bin/virtual-you-server seed-demo
.venv/bin/virtual-you-server serve
```

Open `http://127.0.0.1:8000`; get the local review key with
`.venv/bin/virtual-you-server show-key`. Demo generation and simulated delivery
are the defaults. No Slack/Discord messages are sent until live delivery is
configured and a specific draft is reviewed, approved, and explicitly delivered.

## Member 4 voice and work questions

[MEMBER4.md](MEMBER4.md) covers local Whisper or optional ElevenLabs speech input,
transcript correction, normalized voice drafts, grounded factual questions and
persistent escalations. Both the web review page and Slack reuse the existing
approval workflow. See the integration audit for current local and live-provider verification.

## Pathway 1 Ingestion

This repository currently implements Member 1's ingestion boundary:

```text
Claude Code / Cursor / Codex / voice transcript
                ↓
        source-specific parser
                ↓
        canonical event stream
                ↓
        session normalization
                ↓
       recursive secret redaction
                ↓
       validated ActivityRecord
                ↓
        sanitized local storage
```

Raw logs are processed in memory. Only records marked `redacted: true` can be
stored or returned to downstream pathways.

See `INGESTION_ARCHITECTURE.md` for the component and sequence diagrams.

## Setup

Python 3.11 or newer is supported; this integration was tested with Python 3.12.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest
```

## CLI

### Docker

The root `Dockerfile` builds the ingestion CLI; the separate
[`Dockerfile.backend`](Dockerfile.backend) builds the web/API backend
(see [BACKEND.md](BACKEND.md#deployment-and-limits)).

Build the CLI image and view its commands:

```bash
docker build -t virtual-you .
docker run --rm virtual-you
```

Mount an input directory read-only and keep sanitized records and checkpoints
in a named volume that persists between runs:

```bash
docker run --rm \
  -v virtual-you-data:/home/app/.virtual-you \
  -v /absolute/path/to/transcripts:/input:ro \
  virtual-you ingest --source voice /input/transcript.txt

docker run --rm \
  -v virtual-you-data:/home/app/.virtual-you \
  virtual-you latest

docker run --rm virtual-you schema > activity-record.schema.json
```

The container runs as a non-root user and installs only runtime dependencies.
Input files must be readable by container UID 10001. For Claude or Cursor,
use the corresponding `--source` value and input file path.

### Local CLI

The same commands work as `virtual-you ...` or `python -m virtual_you ...`.

Ingest complete session files:

```bash
python -m virtual_you ingest --source claude /path/to/session.jsonl
python -m virtual_you ingest --source cursor /path/to/agent-transcript.jsonl
python -m virtual_you ingest --source cursor /path/to/state.vscdb
python -m virtual_you ingest --source codex /path/to/rollout.jsonl
```

Ingest the newest local session for a source (by file mtime; Claude/Cursor
skip `subagents/` transcripts):

```bash
python -m virtual_you ingest --source claude --latest
python -m virtual_you ingest --source cursor --latest
python -m virtual_you ingest --source codex --latest
```

Ingest a voice transcript saved as plain text. Voice cannot use `--latest`:

```bash
python -m virtual_you ingest --source voice /path/to/transcript.txt
```

Process newly appended JSONL records once:

```bash
python -m virtual_you watch --source claude /path/to/session.jsonl
python -m virtual_you watch --source cursor --latest
```

Show the latest sanitized activity:

```bash
python -m virtual_you latest
```

Export the shared JSON Schema for other team members:

```bash
python -m virtual_you schema > activity-record.schema.json
```

By default, sanitized records and checkpoints are kept in
`~/.virtual-you/`. Override with `--data-dir` or `VIRTUAL_YOU_DATA_DIR`.
No network service is used by Pathway 1.

## Public integration API

```python
from virtual_you.ingest import IngestionService

service = IngestionService()
record = service.ingest_file("claude", "/path/to/session.jsonl")
latest = service.latest_activity()
```

Downstream members should import `ActivityRecord` from
`virtual_you.contracts`. They must not read Claude/Cursor logs directly.

Available operations:

- `ingest_file(source, path)`
- `ingest_transcript(source, text)`
- `watch(path=..., source=...)`
- `latest_activity()`
- `export(destination=None)`

## Stable errors

`IngestionError.code` is one of:

- `nothing_to_report`
- `malformed_input`
- `unsupported_event`
- `source_not_found`
- `unsafe_output`
- `storage_error`

Errors may contain a line number, but never include the raw event or secret.

## Source behavior

### Claude

The adapter reads `~/.claude/projects/**/<session-id>.jsonl` and supports
messages, text/thinking blocks, tool calls/results, file changes, and session
results. Non-activity Claude metadata records are ignored.

### Cursor

The adapter prefers current agent-transcript JSONL. It can fall back to
read-only extraction from Cursor's `state.vscdb` `ItemTable`; it never writes
to Cursor's database.

### Codex

The adapter reads `~/.codex/sessions/**/rollout-*.jsonl` and maps user
turns, exec/tool calls, and apply_patch file changes into the same
activity contract.

### Voice

Member 4 owns recording and speech-to-text. Pathway 1 accepts only the
resulting text and normalizes it into the same activity contract.

## Safety tests

The test suite covers:

- Real-shaped Claude and Cursor fixtures
- Recursive nested redaction
- Known provider token formats
- Generic credentials and authorization headers
- PEM private keys and sensitive URL parameters
- High-entropy values
- Malformed and empty sources
- Partial JSONL lines and restart checkpoints
- Duplicate-free incremental ingestion
- Redaction before persistence and export
- Read-only Cursor SQLite access

Run the acceptance suite:

```bash
.venv/bin/pytest --cov=virtual_you --cov-report=term-missing
```

Persona generation, LLM drafting, approval, and Slack/Discord delivery are
implemented separately in `virtual_you.backend`; they consume this ingestion
boundary. Member 4 adds speech-to-text and Slack question routing on top of it;
Optional GitHub, Jira and Drive enrichment is available behind configuration flags;
a Discord inbound listener remains follow-up work.

## Slack-native headless workflow

The existing Slack agent is integrated under [slack_agent/](slack_agent/WORKFLOW.md). Select a person once in Slack Home to build a persona from your own DM history, automatically prepare activity-based drafts, and review them with Slack buttons and modals. One-time user OAuth is required for private DM history. The Slack process runs the backend heartbeat in-process; no dashboard is required.

The phase-2 Slack experience adds browser OAuth (no user-token copying), source opt-in, recipient/project policies, editable style review, and an opt-in message shortcut. A local macOS menu-bar companion controls connection and the login service. See [the workflow guide](slack_agent/WORKFLOW.md) for operator setup and distribution limits.

### Voice memos and owner escalation

Develop includes the selectively integrated Member 4 features: reviewed speech-to-text
memos feed the existing evidence pipeline, while uncertain colleague questions appear
in the owner's Slack inbox. Current personas, low-history defaults, GPT-4o mini/RAG,
and approved personal-DM replies are preserved. See [MEMBER4.md](MEMBER4.md).
