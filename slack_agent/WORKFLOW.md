# VirtualYou: Slack first, headless backend

Open the VirtualYou app in Slack → Home → **Choose a person**. The app reads the latest 10–20 eligible messages **you wrote** in your existing DM with that person, redacts recognizable secrets, and creates a recipient-specific persona and private `soul.md`. Their replies are excluded. It needs at least ten usable examples; it never invents missing history.

Your selection survives restarts. With automatic drafts enabled, the backend refreshes Member 1's activity files every 30 seconds, waits for 60 seconds of quiet, and prepares a draft from new activity in the last 24 hours. It waits at least ten minutes between automatic drafts and holds further drafts while one awaits resolution. Timings are configurable. This refreshes normalized ingestion output; Member 1's ingestion process must continue producing it. Persona history is read on selection or **Refresh style**, not on every heartbeat.

A private card appears in your bot DM with **Approve & send**, **Edit**, **Regenerate**, and **Reject**. Edit opens a Slack modal. Saving or regenerating needs fresh approval. The selected person receives nothing until approval; delivery uses the bot identity, not impersonation of your personal account. Ambiguous delivery requires a manual check in the actual conversation before retrying. Unknown card delivery is not blindly retried; drafts remain accessible in Home.

Slack cannot spontaneously open a modal: a user click supplies the short-lived trigger. This implementation therefore uses Slack's existing app icon, notifications, cards and modals. It does not add a browser extension or macOS menu-bar app. No separate dashboard or HTTP backend is required in Socket Mode.

## One-time setup on this Mac

The Slack CLI is already installed; it is not required to run the background service. Apply `manifest.json` to your Slack app and reinstall/re-authorize after scope changes. Keep App Home enabled. The optional Agents & AI app presentation may depend on your Slack plan; the review workflow uses ordinary Home, DM cards and modals.

From `/Users/a/Downloads/virtualyou`:

```bash
uv pip install --python .venv/bin/python -e '/Users/a/Downloads/MakeNoMistake[backend]' -e '.[test]'
cp .env.sample .env
chmod 600 .env
```

For a fresh checkout of this repository's `slack_agent/` directory, create a Python 3.12 virtual environment there, then install the parent backend and this package (`pip install -e '..[backend]' -e '.[test]'`). The backend dependency is local source, not a similarly named package downloaded from PyPI.

Fill `.env` once:

- `VIRTUAL_YOU_SLACK_OWNER` and `VIRTUAL_YOU_SLACK_TEAM`: your actual member and workspace IDs.
- `SLACK_APP_TOKEN`: app-level token with `connections:write`; `SLACK_BOT_TOKEN`: installed bot token.
- `SLACK_USER_TOKEN`: your user OAuth token with `im:read`, `im:history`, `users:read`. A bot token cannot read your existing human-to-human DMs. Only connect history you have permission to access. Do not commit tokens.
- `VIRTUAL_YOU_ACTIVITY_DIR`: absolute directory containing Member 1's normalized `activity-*.json` output.
- Model settings: `demo` is an offline extractive preview. For generated prose, use `openai` with a model and `OPENAI_API_KEY`, or `ollama` with a locally available model. See the parent `BACKEND.md`.
- `VIRTUAL_YOU_LIVE_DELIVERY=false` initially simulates recipient delivery. Review cards still go to your own bot DM when connected. Set `true` after checking the setup to enable approved recipient delivery.

Run `.venv/bin/python app.py`, open VirtualYou Home in Slack, and choose a person. Stop that foreground process before installing the service:

```bash
.venv/bin/python scripts/macos_service.py install
.venv/bin/python scripts/macos_service.py status
# Later, to stop and remove login startup while preserving data:
.venv/bin/python scripts/macos_service.py uninstall
```

The login service reads `.env`, starts at login, and restarts after process failure. This Mac must be awake, logged in, and connected. For 24/7 operation, host the process and ingestion on an always-on machine. Do not run a second backend or Slack process against the same data directory. Keep `.virtual-you/` private and persistent. It contains personas, drafts, sanitized queued examples and OAuth installations; completed/failed history jobs drop their sample payloads.

## Optional browser OAuth instead of copying the user token

`app_oauth.py` supports one-time browser authorization and persists the configured owner's installation. Configure client ID/secret, signing secret, and a real HTTPS `SLACK_REDIRECT_URI` ending `/slack/oauth_redirect`. Replace the manifest's example URLs with your hosted/tunneled URLs. For HTTP mode disable Socket Mode, route events and interactions to `/slack/events`, and run `.venv/bin/python app_oauth.py` on port 3000. Visit `/slack/install` and authorize as the configured owner. The OAuth HTTP process also runs the headless worker.

Alternatively complete OAuth once, stop the HTTP process, re-enable Socket Mode and use `app.py` with the same persistent data directory. A valid bot token and app token are still needed for Socket Mode startup; the worker reads the saved owner's user token. Run exactly one entry point. This is a single-owner installation, not a multi-tenant SaaS onboarding service. Expired/revoked tokens require reconnecting; automatic OAuth token rotation is not implemented.

## Team integration and validation

The tracked copy lives under `slack_agent/` on `phase-1,-soul+Draftending-A/D`. The existing standalone `/Users/a/Downloads/virtualyou` folder is updated too. Avoid developing independently in both copies. The original unrestricted MCP agent modules remain as reference; default listener registration uses only the approval workflow.

Run `.venv/bin/python -m pytest` in this folder and the parent repository's backend tests separately. Tests use fake Slack clients and model/delivery transports; passing tests does not prove installation permissions in your real workspace. No real history was read and no real Slack messages were sent during implementation.

References: [Slack CLI installation](https://docs.slack.dev/tools/slack-cli/guides/installing-the-slack-cli-for-mac-and-linux), [Slack agents](https://docs.slack.dev/ai/developing-agents), [history token access](https://docs.slack.dev/reference/methods/conversations.history/), [native modals](https://docs.slack.dev/surfaces/modals/).
