# Virtual You — Four-Member Delivery Plan

This plan follows the four pathways defined in the initial `AGENT.md`. Each
member owns one pathway, its tests, documentation, and demonstration.

## Shared Contract — All Members, Hour 0–1

Before working separately, freeze these shared interfaces:

- `ActivityRecord`
- `PersonaProfile`
- `DraftRequest`
- `DraftReport`
- `ApprovalDecision`
- `DeliveryReceipt`

The normalized activity record must contain:

```text
start_state
prompts[]
reasoning_summary
files_changed[]
diffs[]
tool_calls[]
end_state
timestamp_range
```

### Shared measurable deliverables

- One sanitized fixture `ActivityRecord`
- One fixture `soul.md`
- Agreed API/function signatures
- Agreed storage locations
- Agreed environment variable names
- One end-to-end test scenario

---

## Member 1 — Pathway 1: Ingestion, Normalization, and Redaction

### Owns

Everything from raw Claude/Cursor activity to a clean `ActivityRecord`.

Voice transcription output enters this member's normalization pipeline, but
Member 4 owns recording and speech-to-text.

### Tasks

1. Build the Claude Code JSONL reader.
2. Extract:
   - User prompts
   - Assistant messages
   - Reasoning summaries
   - Tool calls and results
   - File edits and diffs
   - Timestamps
3. Build the Cursor transcript reader:
   - Agent transcript JSONL first
   - `state.vscdb` only if needed
4. Normalize both sources into the shared schema.
5. Add secret redaction:
   - API keys
   - Tokens
   - Passwords
   - Authorization headers
   - High-entropy strings
6. Add explicit handling for:
   - Empty sessions
   - Invalid JSONL
   - Unsupported records
   - Sessions with nothing reportable
7. Provide an ingestion CLI or API.

### Tech stack

- Python 3.12
- Pydantic
- `pathlib` and `json`
- `watchdog`
- Built-in `sqlite3`
- Regex plus entropy detection
- pytest

### Measurable deliverables

- Claude parser supports at least **one real session**
- Cursor parser supports at least **one real session**
- Both produce the same `ActivityRecord` structure
- At least **10 redaction test cases**
- At least **3 malformed-input test cases**
- One planted API key is absent from every output field
- Parser reports "nothing to report" for an empty session
- CLI command:

```bash
python -m virtual_you ingest --source claude session.jsonl
```

### Definition of done

Given one real Claude session and one real Cursor session, the component
produces valid, redacted activity records without exposing secrets.

### Handoff

```text
Member 1 → ActivityRecord → Members 2, 3, and 4
```

---

## Member 2 — Pathway 2: Persona Layer and Prompt Assembly

### Owns

Creating recipient-specific `soul.md` files and combining them with activity
records.

### Tasks

1. Build a minimal form or script that accepts **10–20 past messages**.
2. Analyze:
   - Tone
   - Formality
   - Greeting style
   - Sign-off style
   - Sentence length
   - Vocabulary
   - Emoji and punctuation habits
3. Generate a local `soul.md`.
4. Include **3–5 verbatim examples** in each profile.
5. Support one profile per recipient.
6. Build the prompt assembler:

```text
soul.md + ActivityRecord → system prompt
```

7. Clearly separate:
   - Facts from the activity record
   - Style from `soul.md`
8. Prevent persona examples from adding factual claims.
9. Keep persona files local and excluded from Git.

### Tech stack

- Python
- Pydantic
- Markdown
- FastAPI endpoint or Streamlit form
- Local/OpenAI model for persona extraction
- pytest

### Measurable deliverables

- Accepts at least **10 sample messages**
- Produces a valid `soul.md`
- Supports at least **two different recipients**
- Each profile includes:
  - Tone descriptors
  - Greeting/sign-off
  - Sentence-style description
  - Vocabulary traits
  - 3–5 examples
- Generates one complete system prompt
- The same activity record produces visibly different tone for two personas
- All factual statements remain identical between the two outputs
- At least **5 persona/prompt tests**

### Definition of done

Given an existing `ActivityRecord` and recipient profile, the component
produces a prompt that preserves activity facts while reflecting the user's
style for that recipient.

### Handoff

```text
Member 2 → AssembledPrompt → Member 3
```

---

## Member 3 — Pathway 3: Draft Engine, Approval, and Delivery

### Owns

The core product demonstration: generate, review, approve, and deliver a
report.

### Tasks

1. Define one provider-independent LLM interface.
2. Connect:
   - GPT-live where voice capability is needed
   - A swappable text model for ordinary drafts
3. Generate reports containing:
   - Starting state
   - Approach and rationale
   - Mid-task changes
   - Final result
   - Links
   - Blockers
4. Build the approval gate:
   - Approve
   - Edit
   - Regenerate
   - Reject
5. Enforce draft states:

```text
pending → approved → delivered
pending → edited → pending
pending → regenerated → pending
pending → rejected
```

6. Integrate Slack as the primary destination.
7. Integrate Discord as the secondary destination.
8. Prevent duplicate delivery.
9. Expose delivery failures clearly.
10. Ensure every outbound message passes through approval.

### Tech stack

- Python and FastAPI
- OpenAI API / GPT-live
- Ollama or another swappable text model
- SQLite
- Slack Bolt for Python
- Discord webhook
- React/Next.js or Streamlit for approval
- pytest

### Measurable deliverables

- One provider-independent `DraftEngine`
- One structured report generated from a real activity record
- Report contains all **six required sections**
- Approval interface supports all **four actions**
- Pending and rejected drafts cannot be posted
- Approved draft posts to a live Slack test channel
- Approved draft posts no more than once
- Discord adapter successfully posts one test message
- Delivery failures are stored and shown
- At least **10 approval/delivery tests**

### Definition of done

A real coding session becomes a persona-matched draft, the user reviews it,
and only the approved version is posted live to Slack.

### Handoff

```text
Member 3 → Approved messages → Slack/Discord
Member 3 DraftEngine ← reused by Member 4
```

---

## Member 4 — Pathway 4: Voice and Asynchronous Virtual You Chatbot

### Owns

The standing Slack/Discord identity, manager questions, voice input, and
escalation.

### Tasks

1. Build an always-on bot process.
2. Listen for manager mentions and questions.
3. Retrieve:
   - Latest `ActivityRecord`
   - Correct recipient `soul.md`
4. Reuse Member 3's `DraftEngine`.
5. Add an evidence check before answering.
6. Support routine questions:
   - What was completed?
   - What changed?
   - Are there blockers?
   - What is the current status?
7. Escalate unsupported questions:
   - Scope decisions
   - Commitments
   - Deadlines
   - Personal opinions
   - Information absent from the record
8. Flag escalated questions for the user.
9. Accept a voice memo.
10. Run speech-to-text and pass the transcript into Member 1's normalizer.
11. Use GPT-live for voice-triggered drafting if time permits.

### Tech stack

- Python
- Slack Bolt Events API
- Discord.py or Discord gateway
- FastAPI webhooks
- OpenAI speech-to-text
- GPT-live
- SQLite
- pytest

### Measurable deliverables

- Bot responds to a Slack mention
- Correctly answers at least **four supported question types**
- Escalates at least **four unsupported question types**
- Every answer cites or derives from the latest activity record
- Unknown questions produce an explicit "I don't have enough information"
- Escalations are stored and visible to the user
- One voice memo is transcribed successfully
- Voice transcript enters the same normalization pipeline
- At least **8 async/voice tests**
- No duplicate LLM-generation logic

### Definition of done

A manager can message Virtual You and receive a grounded response from recent
activity, while unsupported questions are escalated rather than fabricated.

---

## Ownership Boundaries

### Member 1 owns the factual boundary

No downstream component reads raw JSONL, SQLite session data, or recordings.

### Member 2 owns the style boundary

Persona changes presentation, not facts.

### Member 3 owns the outbound boundary

No Slack or Discord message bypasses approval.

### Member 4 owns the uncertainty boundary

No unsupported manager question receives a fabricated answer.

---

## 24-Hour Schedule

### Hours 0–1

All members freeze contracts and fixtures.

### Hours 1–7

Each member builds independently:

- Member 1 uses real logs
- Members 2–4 use fixtures

### Hour 7 integration

```text
Member 1 ActivityRecord → Member 2 Prompt → Member 3 Draft
```

### Hour 12 milestone

Required core demo:

```text
Real session → redacted record → persona draft → approval → Slack
```

### Hours 12–18

- Cursor ingestion
- Discord
- Async manager questions
- Escalation
- Voice memo transcription

### Hour 18 integration

Connect Member 4 to the shared activity store, persona store, and draft engine.

### Hours 18–21

- Failure handling
- End-to-end tests
- Security review
- Demo data

### Hours 21–24

- Demo rehearsal
- Slides
- README
- Backup recording
- MCP architecture slide only

---

## Final Team Acceptance Test

The project is complete when the team can demonstrate:

1. Parse a real Claude session.
2. Parse a real Cursor session.
3. Remove a planted secret.
4. Generate a valid `ActivityRecord`.
5. Apply the correct recipient's `soul.md`.
6. Generate all six report sections.
7. Edit and approve the draft.
8. Post it exactly once to Slack.
9. Answer a supported manager question.
10. Escalate an unsupported manager question.
11. Convert one voice memo into a draft.
12. Confirm no outbound path bypasses approval.

The optional GitHub/Jira MCP module remains a design slide unless every item
above is complete.
