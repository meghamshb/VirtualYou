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

## Pathway 1 Ingestion

This repository currently implements Member 1's ingestion boundary:

```text
Claude Code / Cursor / voice transcript
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

Python 3.9 or newer is supported.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest
```

## CLI

Ingest complete session files:

```bash
.venv/bin/virtual-you ingest --source claude /path/to/session.jsonl
.venv/bin/virtual-you ingest --source cursor /path/to/agent-transcript.jsonl
.venv/bin/virtual-you ingest --source cursor /path/to/state.vscdb
```

Ingest a voice transcript saved as plain text:

```bash
.venv/bin/virtual-you ingest --source voice /path/to/transcript.txt
```

Process newly appended JSONL records once:

```bash
.venv/bin/virtual-you watch --source claude /path/to/session.jsonl
```

Show the latest sanitized activity:

```bash
.venv/bin/virtual-you latest
```

Export the shared JSON Schema for other team members:

```bash
.venv/bin/virtual-you schema > activity-record.schema.json
```

By default, sanitized records and checkpoints are kept in
`~/.virtual-you/`. No network service is used by Pathway 1.

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
boundary. Speech-to-text, standing chatbot listeners, and MCP enrichment remain
outside the ingestion/backend work described here.

## Slack-native headless workflow

The existing Slack agent is integrated under [slack_agent/](slack_agent/WORKFLOW.md). Select a person once in Slack Home to build a persona from your own DM history, automatically prepare activity-based drafts, and review them with Slack buttons and modals. One-time user OAuth is required for private DM history. The Slack process runs the backend heartbeat in-process; no dashboard is required.
