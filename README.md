# Virtual You — Pathway 1 Ingestion

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

Persona generation, LLM drafting, approval, Slack/Discord delivery,
speech-to-text, and MCP enrichment are intentionally outside this pathway.
