> **Integration update (2026-09-20):** These tools are now reconciled on `leo-dev`
> with the current shared persona service, formal fallback below ten messages,
> style revision/history/undo, and the current drafting and approval paths.
> See [the integration audit](docs/LEO_INTEGRATION.md). The original migration below
> describes its historical branch baseline; profiles may now start with 0–20 messages.

# Member 2 — Persona layer and prompt assembly

This branch starts from Member 1's phase 1.1 ingestion commit `ab3c90f` and
merges the existing Member 2 branch (`95ebbf1`), including its shared backend
(`2894684`) and Slack-native workflow (`ba6031b`) dependencies. It reuses the team's `ActivityRecord`,
`PersonaProfile`, provider interface, and `AssembledPrompt`. It does not add a
second drafting engine or modify ingestion, approval, or delivery policy.

## Run the acceptance demo

```bash
uv sync --extra backend --extra dev --python 3.12
uv run python scripts/demo_member2.py
```

Without uv, install `.[backend,dev]` in a Python 3.12 virtual environment and
run the same script with that environment's Python.

The script creates **two profiles from ten synthetic messages each**, exports
five sanitized verbatim examples per profile, reloads their `soul.md` files,
assembles two complete prompts from the same activity fixture, and calls the
existing `DraftEngine` using `demo:extractive`. It asserts that the evidence and
six factual report sections are identical while the greetings/sign-offs differ.
Historical facts planted in the messages must not appear in either preview.

Outputs are private, ignored files under `.virtual-you/member2-demo/demo/`:

- `manager/` and `teammate/`: `soul.md`, `prompt.json`, `preview.txt`, `report.json`.
- `acceptance.json`: the check results and provider label.

Open the two `preview.txt` files side by side for the team demonstration.
This is an offline, deterministic acceptance demo. It does not call an external
model, create an approved draft, or send any Slack/Discord messages.

## Create an actual recipient profile

Use the existing web form at `http://127.0.0.1:8000` (see [BACKEND.md](BACKEND.md)),
or save 10–20 consented messages as a JSON array in ignored `.virtual-you/` and run:

```bash
uv run python -m virtual_you.persona create \
  --recipient manager --name Manager \
  --messages .virtual-you/manager-messages.json
```

Each message must contain 1–4,000 characters and cannot be blank. JSON input
preserves multiline messages and whitespace; the minimal web form accepts one
message per line. All messages inform the analysis; the first five sanitized
messages become the profile's examples. Redaction is the only intentional
change to their content. Input and validation errors do not echo message text.

Traits include tone, formality, greeting, sign-off, sentence length/structure,
vocabulary, punctuation, and emoji frequency. The local demo extractor reports
measured patterns and uses a small list of stylistic words instead of copying
project names into vocabulary. It is an English heuristic baseline, not an LLM.

For model-based extraction, reuse the backend's existing configuration:

| Variable | Purpose |
|---|---|
| `VIRTUAL_YOU_DATA_DIR` | Local store; default `.virtual-you` |
| `VIRTUAL_YOU_LLM_PROVIDER` | `demo` (default), `ollama`, or `openai` |
| `VIRTUAL_YOU_LLM_MODEL` | Available model identifier for Ollama/OpenAI |
| `VIRTUAL_YOU_OLLAMA_URL` | Local Ollama endpoint; default `http://127.0.0.1:11434` |
| `OPENAI_API_KEY` | Only required for OpenAI |

The CLI loads `.env` and also accepts `--data-dir PATH` before the subcommand.
Selecting OpenAI sends the **redacted** examples to OpenAI; selecting a local
Ollama server keeps model processing local. No private messages are fetched
automatically. There is no silent switch to demo if a configured model fails.

Storage is `<data-dir>/personas/<first-24-characters-of-sha256(recipient-id)>/soul.md`
plus the existing `backend.sqlite3` profile store. Same-recipient updates
increment that profile's version; other profiles remain untouched. Profile
directories are mode 0700; files are written atomically with mode 0600 from the
moment their temporary file is created. `.virtual-you/`, SQLite state, and all
`soul.md` files are Git-ignored. Use the ignored default for real messages.

The committed files under `examples/persona/` are explicitly synthetic fixtures,
including [soul.example.md](examples/persona/soul.example.md). They contain no
teammate's real messages or credentials.

## Member 3 handoff

```python
from pathlib import Path
from virtual_you.contracts.activity import ActivityRecord
from virtual_you.backend.soul import profile_from_soul
from virtual_you.backend.prompts import assemble_activity_prompt

profile = profile_from_soul(
    Path("/private/path/to/soul.md").read_text(), recipient_id="manager"
)
activity = ActivityRecord.model_validate_json(
    Path("/private/path/to/activity.json").read_text()
)
prompt = assemble_activity_prompt(profile, activity, recipient_id="manager")
# prompt.system, prompt.user, prompt.evidence, prompt.persona_version
```

`assemble_activity_prompt(profile, activity, *, recipient_id, question=None)`
does not need a store or provider. It rejects recipient mismatch, missing
explicit `redacted: true`, and empty activity. It applies the existing redactor
again, retains Member 1's schema, and uses the existing evidence mapper and IDs.
That mapper bounds long records; absent or truncated evidence must not be
invented. The original record and persona objects are not modified.

The existing drafting engine imports `assemble_prompt(profile, evidence,
question=None)` from the new `backend.prompts` module. Its original import path
through `backend.drafting` remains available for compatibility. Member 3 keeps
ownership of model generation, grounding validation, review, and delivery.

HTTP alternative (requires the same bearer owner key as the existing backend):

```text
POST /api/prompts/assemble
{
  "recipient_id": "manager",
  "activity": { ...the complete ActivityRecord with "redacted": true... },
  "question": null
}
→ AssembledPrompt {system, user, evidence, persona_version}
```

This endpoint does not persist the activity, call a model, save a draft, or
send anything. It resolves the matching recipient profile from the local store.
Its request schema is available through FastAPI's `/docs`.

Local file alternative:

```bash
uv run python -m virtual_you.persona assemble \
  --recipient manager \
  --activity tests/fixtures/activity_record.json
# Optionally add --soul /private/path/to/soul.md instead of loading from SQLite.
```

The resulting prompt is saved privately in `<data-dir>/prompts/`; stdout shows
its location, not the imported messages. Assembly works without model credentials.

## Style and facts boundary

Trusted rules and the report schema stay in `system`. The user JSON separates
`style_only`, `style_examples_not_facts`, `evidence`, and `question`. Only evidence
can support current facts. An example saying a release shipped last Friday must
never add that claim to today's report. Imported text cannot authorize tools,
approval, delivery, or new commitments.

Because Member 3 appends greetings and sign-offs directly, these fields are
restricted to generic English courtesy phrases (including empty). The extraction
prompt and JSON schema advertise the choices; invalid model values are rejected
before storage. Older stored profiles containing free-form salutations need to
be recreated. Other style traits remain descriptive, untrusted data.

New `soul.md` exports contain a fenced JSON profile under `Portable profile
(JSON)`. The loader validates this block and the recipient, then regenerates
the readable Markdown. It never promotes free-form Markdown to system
instructions. Editing the structured block affects a standalone loaded profile;
it does not silently update SQLite. Legacy exports without this block should be
recreated through the form or CLI. Unknown formats fail with `invalid_soul`.

Separation and citation checks reduce contamination; they do **not** prove
semantic truth for arbitrary LLM output. Exact factual invariance is verified
for the deterministic demo. Live OpenAI/Ollama output still requires Member 3's
grounding checks and human review. Live model quality is not claimed by these tests.

## Acceptance and verification

The paired evaluation runner now exercises report-body style and controlled
factual cases through Member 3's pending-draft workflow. See
[MEMBER2_ACCEPTANCE.md](MEMBER2_ACCEPTANCE.md) for commands, review artifacts,
failure statuses, and current evidence. It never labels offline checks as live
model acceptance.

The implementation paths for Member 2 in
[TEAM_DELIVERABLES.md](TEAM_DELIVERABLES.md) exist, but offline tests do not
establish the complete persona-quality acceptance criteria.

| Deliverable | Current evidence | Remaining acceptance work |
|---|---|---|
| Input of 10–20 messages | Existing web form/API; added local CLI; count tests pass | Exercise the chosen demo onboarding flow |
| Tone, formality, greetings, sentence style, vocabulary, emoji/punctuation | Local heuristic extraction tested; OpenAI/Ollama adapters available | Review extraction from a real model |
| Local `soul.md` with 3–5 examples | Five sanitized verbatim examples; round-trip and private-file tests pass | Review whether the selected examples represent the intended persona |
| Two recipient profiles | Isolation and versioning tests pass | Judge whether each generated voice is recognizable |
| Complete assembled prompt | Python, CLI, and authenticated API verified | Exercise with the team's selected real normalized activity |
| Facts/style separation | Data separation, redaction, injection cases, and safe salutations tested | Evaluate factual preservation in real model drafts |
| Visibly different tone with identical facts | Deterministic demo has identical bodies; only greetings/sign-offs differ | Demonstrate substantive differences in wording, sentence style and formality without changing claims |
| Local storage excluded from Git | Ignore and permission checks pass | Keep actual messages/profiles in ignored storage |
| At least five tests | 35 new Member 2 cases, plus inherited persona tests | No additional test count is needed; live quality evidence is the gap |

Before calling Member 2 demo-ready, run both personas through the selected
model on the same normalized activity; compare all six sections against the
source, including missing information, failed tests, numbers, names, URLs and
commitments. Compare sentence structure, formality and vocabulary beyond the
salutations. Then rehearse that profile-to-prompt-to-draft handoff with Member 3
using the selected demo record. The deterministic demo is a regression check,
not evidence that a model has learned a convincing personal writing style.

The inherited backend already supplied profile creation, provider calls, local
storage, a minimal form, and draft-engine integration. This branch extends those
with portable profiles, a standalone CLI/API handoff, tighter style boundaries,
fixtures, paired evaluation, documentation, and dedicated tests. The total regression count
below includes other members' existing tests; it is not a count of new tests.

```bash
uv run pytest tests/backend/test_member2.py tests/backend/test_persona_drafting.py
uv run pytest
```

The Member 2 tests cover count validation (including the existing 0/9/21 cases),
twenty-message analysis, verbatim multiline/Unicode round-trip, profile versions,
recipient mismatch, malformed exports, injected example claims, secret redaction
before provider calls, unsafe salutations, identical evidence, private file
permissions, Git exclusions, CLI use, API authentication, empty/unredacted input,
and the synthetic end-to-end comparison. Existing draft/approval/delivery and
ingestion tests remain the regression boundary.

Verified after the phase 1.1 integration on Python 3.12: **188 tests passed**;
the separate synthetic demo passed. Live OpenAI/Ollama style quality and live
messaging were not exercised. The test runner reports two pre-existing
FastAPI/Starlette dependency deprecation warnings.

After integrating `ba6031b`, the Slack persona/history and coordinator tests
also passed (**18 cases**, fake Slack clients and HTTP transports):

```bash
PYTHONPATH=slack_agent uv run --with slack-sdk==3.44.1 pytest \
  slack_agent/tests/test_workflow_coordinator.py \
  slack_agent/tests/test_workflow_history.py
```

## Phase 1.1 integration

The ingestion parsers, CLI, `ActivityRecord`, fixtures, and root `Dockerfile`
are unchanged from `ab3c90f`. The backend accepts the new `codex` source and
keeps `source_path` as local provenance, outside model prompts. Visible
explanations may support an approach; the omission marker alone remains unknown.
Integration tests use isolated workspaces so Git overlays and local environment
files cannot contaminate the fixture inputs.

Use `Dockerfile.backend` for the backend image. See [docs/STATUS.md](docs/STATUS.md)
for migration checks and remaining live acceptance work.
