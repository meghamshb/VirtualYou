# Virtual You

Your work leaves a trail. Your teammates shouldn't have to read the whole trail to know what happened.

Virtual You turns work activity into updates and answers that sound like you — **but don't go out without you**. It gathers signals from coding sessions, Git, voice notes, and selected project integrations; redacts secrets before storing activity; then uses the relevant context to draft for a specific person or question. You review the result before anything is sent.

I'm interested in the space between “AI wrote a summary” and “I'd actually trust this to speak for me.” That means grounding a claim in real work, keeping projects and recipients separate, and making approval a real step rather than a decorative button.

Under the hood: Python/FastAPI for ingestion, retrieval, drafting, and review; Electron for the desktop app; Slack for in-conversation workflows. The local and integration paths work, while hosted distribution and some live-service acceptance are still in progress.

[how it fits together ↗](docs/DEVELOP_INTEGRATION.md) · [ingestion and redaction ↗](INGESTION_ARCHITECTURE.md) · [current status ↗](docs/STATUS.md) · [license ↗](LICENSE)
