# Member 2 acceptance: evidence, not just a green test count

The persona implementation is available. Live writing-style quality and
semantic factual preservation still require evaluation with the selected model.
This document records what the acceptance runner can establish and what it cannot.

## Paired evaluation runner

```bash
# Uses the configured OpenAI or Ollama model. This makes model requests.
uv run python scripts/evaluate_member2.py

# Explicit offline rehearsal: never reported as live-model acceptance.
uv run python scripts/evaluate_member2.py --offline
```

Configure the existing `VIRTUAL_YOU_LLM_PROVIDER`, `VIRTUAL_YOU_LLM_MODEL`, and
provider credentials/URL in local `.env` as described in [MEMBER2.md](MEMBER2.md).
A live run with demo/default configuration exits with `blocked`; it does not
silently fall back. There are no automatic paid retries. The default run makes
two persona-extraction calls and six draft-generation calls.

The runner uses `PersonaService`, the existing `DraftEngine`, and Member 3's
`Workflow.create`. It creates pending drafts in a new isolated directory and
forces simulated delivery settings. It never calls approve or deliver.
Existing application storage is not reused. All output stays under ignored,
private `.virtual-you/member2-evaluation/<run-id>/`.

The supplied cases cover:

| Case | What to check |
|---|---|
| Completed callback work | Same completed work; unavailable rationale, links and blockers remain unknown |
| Failed tests | Two failures remain failures; the change is not deployed and Maya's review is still needed |
| Missing result | A requested test run never becomes a passed test or a completed change |

The historical sample messages deliberately contain unrelated projects,
numbers and a Friday release claim. Those must not become current facts.

For the team's chosen record and representative messages:

```bash
uv run python scripts/evaluate_member2.py \
  --activity /private/path/to/normalized-activity.json \
  --manager-messages /private/path/to/manager-messages.json \
  --teammate-messages /private/path/to/teammate-messages.json
```

The optional record must be a normalized `ActivityRecord` with explicit
`redacted: true`. It is marked `user_supplied_normalized_record`, not assumed
to be a genuine real session solely because it was supplied. A custom record
gets paired evidence/body/numeric checks; its facts need a reviewer because
the bundled cases' exact fact expectations do not apply to it. The optional
message files are JSON arrays of 10–20 consented messages each. No raw session
logs or private messages are fetched automatically.

## Inspect the output

- `evaluation.json`: provider/mode, case results, flags and pending human review.
- `profiles/*.md`: the extracted style and sanitized verbatim examples.
- `<case>/source.json`: the normalized input for comparison.
- `<case>/manager.json`, `teammate.json`: complete pending drafts and citations.
- `<case>/review.md`: both messages, body metrics and a reviewer checklist.

The style comparison excludes greetings and sign-offs. It records report-body
variation, approximate sentence length, contractions, exclamations and emoji.
These measurements help a reviewer; none establishes that a persona is
convincing. An unchanged report body is explicitly flagged.

Automatic factual checks look for expected fixture facts, invented numbers,
unrelated historical phrases, and missing information turned into claims.
They also verify both recipients received the same evidence. These are lexical
diagnostics, with possible false positives and false negatives. For example,
legitimate arithmetic may introduce a number not written literally in the
source. Passing checks is not proof of semantic entailment.

Every case needs a human to confirm:

1. Both versions contain the same supported facts and preserve uncertainty.
2. Neither adds claims from old examples, including promises and deadlines.
3. The styles are recognizably different beyond salutations, while technical
   names, numbers, links and test outcomes remain accurate.
4. The examples and extracted profile represent the intended recipient style.

Statuses are deliberately limited:

| Status | Meaning |
|---|---|
| `blocked` | Configuration or input is missing/invalid; no successful model run claimed |
| `generation_failed` | A provider/profile/draft failed; inspect stable error codes |
| `needs_live_model` | Explicit offline rehearsal or injected test double only |
| `review_flags` | Live generation completed, but automatic checks flagged issues |
| `needs_human_review` | Live generation completed without automatic flags; human acceptance is still pending |

The CLI exits 1 for blocked/generation failure, 2 for live review flags, and 0
when a run completes without those errors. Exit 0 is **not** an acceptance pass.
The tool never writes an automatic `accepted` result.

## Current verification

Verified locally: **147 backend/ingestion tests** and **18 Slack workflow tests**
passed. The latter use fake clients/transports. Sixteen new evaluation cases
cover the diagnostics and handoff behavior; no live-model success is inferred.

- Offline runner: three paired cases, six pending drafts, no delivery actions.
- All pairs receive identical activity evidence.
- The offline provider has no body variation; correctly flagged as
  `no_body_style_variation` and `needs_live_model`.
- Adversarial tests demonstrate detection of failed→passed rewrites, invented
  numbers, old project claims, and fabricated results despite valid quote IDs.
- Both committed Claude and Cursor JSONL fixtures traverse Member 1 ingestion,
  Member 2 profiles and Member 3 pending-draft creation successfully. These are
  fixture integration checks, not proof using the team's selected real session.
- A missing provider produces an explicit blocked report. No live model quality,
  real personal-message match, live Slack send, or deployment is claimed.

Remaining: configure the team's text model, run the paired evaluation, inspect
and tune real outputs if needed, then rehearse with the selected normalized
session and representative consented messages. Coordinate any changes to the
draft engine's semantic validation with Member 3; this evaluator does not replace
that engine or its approval gate.
