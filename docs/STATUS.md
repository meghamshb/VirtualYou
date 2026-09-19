# Member 2 migration status — 2026-09-19

Branch: `feat/member-2-persona-phase-1.1`.

## Integration basis

Created from `phase-1.1,-complete-injestion-pipeline` at `ab3c90f`, then merged
`feat/member-2-persona` at `95ebbf1`. The original branch is preserved.
The merge includes the previously shared backend (`2894684`) and Slack workflow
(`ba6031b`) required by Member 2; these are inherited dependencies, not all new
Member 2 implementation. The separate `phase-2-slack-experience` branch is not
part of this migration.

Member 1's ingestion source, CLI, ActivityRecord contract, fixtures/tests and
root Dockerfile remain unchanged from phase 1.1. The backend image is now in
`Dockerfile.backend`, with the phase 1.1 build-context allowlist preserved.
Backend compatibility changes recognize visible approach explanations without
treating a private-reasoning omission marker as a rationale. Codex integration
and exclusion of local `source_path` from model prompts have regression coverage.

## Verified locally

- `uv run pytest`: **188 passed** on Python 3.12.
- Slack coordinator/history suite: **18 passed**, using fake clients/transports.
- `scripts/demo_member2.py`: two synthetic recipients, identical evidence and
  factual report, distinct greetings/sign-offs, zero outbound messages.
- `scripts/evaluate_member2.py --offline`: three completed pairs; correctly
  reports `needs_live_model` with human acceptance pending.
- Ruff lint passes for backend, persona CLI, reporting contract, backend tests,
  and both Member 2 scripts. Formatting passes for the six Python files changed
  in this migration. Broader formatting still flags two inherited files:
  `backend/workflow.py` and `tests/backend/test_member2.py`.
- No merge conflicts or whitespace errors remain. Phase 1.1 ingestion and its
  Dockerfile were compared against `ab3c90f` with no differences.

Both test suites report the existing FastAPI/Starlette dependency deprecations.
Docker image builds were not run: the installed Docker CLI could not connect
to the local daemon. No live model call, Slack/Discord send, or deployment was
performed. Approval and delivery behavior is unchanged.

## Remaining acceptance

Configure the team's selected text model, run the paired evaluation with the
chosen normalized session and representative consented messages, and review
persona quality and factual accuracy. Follow [MEMBER2_ACCEPTANCE.md](../MEMBER2_ACCEPTANCE.md).
The local concept video and private runtime outputs are outside this migration.
