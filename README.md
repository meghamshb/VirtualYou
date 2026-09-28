# Virtual You

> An AI work companion that learns the context behind your updates — and still lets you decide what gets sent.

Virtual You turns work activity into reviewable updates and grounded answers for people you collaborate with. It can collect signals from development work, voice notes, and selected integrations; organize them by project; draft in a recipient-appropriate style; and route the result through human approval. The goal is to make “what changed?” easier to answer without making an AI system speak for you unchecked.

## How it works

**Capture → understand → draft → review → deliver**

- **Capture:** local adapters read supported Claude Code, Cursor, Codex, Git, and voice-transcript sources. Optional read-only GitHub, Jira, and Drive enrichment adds project context when configured.
- **Understand:** events are normalized into activity records. Secrets are redacted before records are stored or exposed to downstream features. Retrieval is scoped to the relevant project and recipient.
- **Draft:** the backend can prepare a work report or answer a factual question from the available evidence, using a recipient profile for tone. It should surface uncertainty rather than inventing a status update.
- **Review:** a person sees the draft, context, and approval decision in the desktop or web review flow. Slack workflows can present the same decision where the conversation is happening.
- **Deliver:** live outbound messaging is opt-in and requires an explicit approval; the portable demo uses simulated delivery only.

Voice is one input, not an automatic send path: the app transcribes a recording through a configured speech provider or local Whisper option, lets the user correct the text, and then sends the normalized note through the same grounding and review process.

## Try it safely

The easiest starting point is the [local Mac demo guide](docs/LOCAL_SETUP.md). It uses synthetic activity and a simulated delivery, so you can explore the desktop app without connecting Slack, providing API keys, or importing your private work history. In short: start the isolated Python demo backend, launch the Electron app, then connect it to the local backend as the guide describes.

`npm run dev` in `desktop/` is a browser **UI preview**, not a connected backend. Use the Electron flow in the guide to see live local activity and approvals. Keep the demo's private data folder and credentials out of Git.

For a deeper look, see the [ingestion architecture](INGESTION_ARCHITECTURE.md), [integrated pipeline](docs/DEVELOP_INTEGRATION.md), [Slack workflow](slack_agent/WORKFLOW.md), and [desktop guide](desktop/README.md).

## Project status

This is a research prototype with working local ingestion, drafting, review, and integration paths. It is **not** a turnkey customer deployment: hosted setup, signed distribution, and parts of live integration acceptance still need work. The [integration audit](docs/LEO_INTEGRATION.md) and [current status](docs/STATUS.md) separate what has been implemented from what has been verified with real services.

## License

See [LICENSE](LICENSE).
