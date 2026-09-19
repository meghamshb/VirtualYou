# VirtualYou: connect once, review in Slack

A bot is a separate Slack identity and cannot read your existing DMs with colleagues. **Connect Slack** requests your user authorization, exchanges Slack's temporary code for tokens, and stores the installation automatically. You never copy messages or user tokens. The user token reads your selected conversation; the bot token delivers an explicitly approved update as VirtualYou. Redaction happens before history samples enter durable jobs or the model. Pattern matching cannot guarantee removal of every confidential detail, so the style review shows retained examples and lets you remove all of them.

## User journey

1. Click **Connect Slack** in the local companion or open the app's `/slack/install` URL. Authorize the configured account/workspace. An administrator may need to approve the app. The OAuth callback validates state and refuses other owners or workspaces.
2. Open **VirtualYou → Home → Setup & status**. Enable the normalized work sources you want used in reports. All start off. This controls retrieval; Member 1's ingestion service controls actual collection. The dialog explains local/cloud processing and offers a global pause.
3. **Organize work** into projects. Use recipient settings to explicitly allow projects, set reporting purpose and minimum cadence. No selected projects means no access, not access to everything.
4. **Choose a person**. The app collects your latest 10–20 eligible messages in that existing DM, excluding their replies and bot messages, and builds their private `soul.md`. At least ten usable examples are required.
5. **Review style**. Correct tone, formality, greeting, sign-off, sentence style, vocabulary, punctuation or emoji. Inspect redacted examples and optionally remove all retained snippets. Save to mark this version reviewed. Refreshing history requires another review.
6. Automatic drafts use only enabled sources and that recipient's projects. A private card offers **Approve & send**, **Edit**, **Regenerate**, and **Reject**. Every delivery requires approval. Changed scope/style or a project reassignment blocks an older draft; reject it and prepare a fresh one.
7. Optionally enable **reply assistance** for a recipient. On their text message in your existing one-to-one DM, choose **Draft update for request** in Slack's message actions. This prepares an evidence-grounded project report addressing the request, not a free-form chat reply. Review it before bot delivery. No background inbox monitoring or automatic replies are enabled.

Selection, settings, profiles, drafts and jobs survive restarts. History is refreshed on explicit selection/Refresh style, not every heartbeat. The default activity heartbeat is 30 seconds, quiet period 60 seconds, automatic interval 10 minutes, and reporting window 24 hours. The scheduler holds further drafts while one awaits resolution. A rate-limited history read resumes from a sanitized checkpoint. An uncertain send is never automatically replayed; verify actual delivery before resolving it.

## Operator setup: once per installation

This version is a single-owner development installation. Developer app credentials and hosting are operator responsibilities; end users do not supply Slack access tokens. It is not yet a multi-tenant hosted service.

Use Python 3.12+ and install from this repository's `slack_agent` folder:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '..[backend]' -e '.[test]'
cp .env.sample .env
chmod 600 .env
```

For the existing standalone `/Users/a/Downloads/virtualyou` copy, install `/Users/a/Downloads/MakeNoMistake[backend]` instead of `..[backend]`. Keep one working copy active at a time.

Configure `.env` with the app's `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_SIGNING_SECRET`, `SLACK_REDIRECT_URI`, and the intended `VIRTUAL_YOU_SLACK_OWNER` / `VIRTUAL_YOU_SLACK_TEAM`. The redirect must be an actual public HTTPS URL ending `/slack/oauth_redirect`, forwarded to this process on port 3000. A stable hosted endpoint or HTTPS tunnel is required; localhost alone is not a Slack OAuth redirect. Do not embed the client secret in a distributed desktop binary.

Update your Slack app from `manifest.json`, replacing all example URLs. For the recommended HTTP/OAuth entry point, **disable Socket Mode** and route events/interactions to `/slack/events`. The message shortcut needs the added `commands` scope; reinstall/re-authorize after manifest changes. Keep App Home enabled. The basic workflow uses standard Home, cards and modals; optional agent presentation can depend on the Slack plan.

Run `.venv/bin/python app_oauth.py` and visit `/slack/install`. This single process hosts OAuth/events and the backend heartbeat. Leave `SLACK_USER_TOKEN`, `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` blank in HTTP mode: OAuth obtains bot and user tokens. Installations are restricted to the configured owner/workspace, stored under the private data directory with directory mode 700 and file mode 600. Local disk encryption is separate. Token rotation is not enabled; revoked/expired access requires reconnecting.

Set `VIRTUAL_YOU_ACTIVITY_DIR` to Member 1's normalized output directory. The worker never reads raw editor logs. For automatic project assignment, use:

```text
activities/
  project-alpha/activity-session-1.json
  project-beta/activity-session-2.json
```

Each direct subfolder becomes a project; new files inherit it automatically. Symlinked files/project directories are rejected. Flat `activity-*.json` files remain supported and can be assigned using Organize work. No changes to the ActivityRecord contract are required. Keep session IDs unique. Project-subfolder assignment is authoritative on file refresh; move the file to change its project permanently. Until ingestion is arranged this way, flat-directory new sessions need explicit assignment.

Choose `VIRTUAL_YOU_LLM_PROVIDER=openai` plus a model/API key, or `ollama` plus a locally available model, for actual generated prose. `demo` is an offline extractive preview. `VIRTUAL_YOU_LIVE_DELIVERY=false` simulates recipient delivery; connected Slack still receives private owner review cards. Set it to `true` only when approved bot delivery is wanted. See the parent `BACKEND.md` for model and ingestion options.

## Headless startup and local menu-bar app

Stop any foreground copy before installing the login service:

```bash
.venv/bin/python scripts/macos_service.py install --mode oauth
.venv/bin/python scripts/macos_service.py status
.venv/bin/python scripts/macos_service.py uninstall
```

The Mac must be logged in, awake and connected, and the HTTPS endpoint must remain available. For 24/7 operation host the worker/ingestion on an always-on machine. Run one worker per data directory. Quitting the menu-bar companion does not stop the backend; choose Stop backend for that.

Build the local companion with Xcode command-line tools:

```bash
.venv/bin/python scripts/build_macos_app.py
open dist/VirtualYou.app
```

Its menu offers Open Slack, Connect Slack, setup/service status, Start backend at login, and Stop backend. The bundle is ad-hoc signed for local development and points to this checkout's Python environment. Keep both in place. It is **not** a self-contained downloadable installer: distribution still needs a bundled runtime, a hosted OAuth service or deliberate deployment model, Developer ID signing/notarization, updates, and multi-user account isolation.

The legacy `app.py` Socket Mode entry point remains available for operators, but requires an app-level token and a bot token at startup. HTTP/OAuth is the default token-free user onboarding path. Never run both against one data directory.

## Validation and current limits

Tests use fake Slack and model/delivery transports, including a real Bolt OAuth callback with mocked code exchange, state-replay rejection, owner/scope checks, source/project filtering, style review, stale-policy approval blocking, and reply opt-in. No actual workspace authorization or live messages were used in development. The menu-bar app can be built/validated locally without granting Slack access.

Refresh uses lexical RAG (SQLite FTS), not embeddings. Project policies scope retrieved evidence; they cannot automatically classify secrets inside arbitrary human edits. Review every outbound draft. Profile removal of snippets updates the current profile and soul export; system backups are outside app control. Group DMs, unsolicited inbox monitoring, Discord onboarding, and a general free-form reply engine are not implemented.

References: [Slack OAuth](https://docs.slack.dev/authentication/installing-with-oauth/), [history token access](https://docs.slack.dev/reference/methods/conversations.history/), [Slack modals](https://docs.slack.dev/surfaces/modals/).

### Opt-in personal DM reply test

Set `VIRTUAL_YOU_DM_WATCH_RECIPIENT` to one selected Slack member ID and restart the OAuth runtime. That person must already have a saved, reviewed profile and an existing human DM. The listener polls only that conversation every 65 seconds using the owner's existing authorization. It starts from activation time; it does not reply to historical messages. Source text is redacted before SQLite persistence and model submission. The configured LLM receives the sanitized incoming message and style descriptors, without retained style examples or work evidence.

The owner receives a review card in their VirtualYou DM. **Approve & send as bot** sends the exact draft from VirtualYou to the colleague's bot DM, not into the human-to-human conversation. Reject sends nothing. This explicitly approved conversational delivery is separate from the work-report preview setting. Pause in Setup stops both preparation and approval sending. Clearing the watch variable and restarting disables the listener. Interrupted or uncertain sends are not replayed; check Slack before manually retrying. SQLite table `slack_dm_replies` records pending, failed, and uncertain states. This first version has no conversational draft editing or multi-message context, and does not provide project updates from RAG.

For lower latency, subscribe to **message.im under user events** in Slack Event Subscriptions (the existing user `im:history` permission is required). Signed events are restricted to the selected person and DM and deduplicated against polling. Polling remains a recovery path; internal workspaces may set `VIRTUAL_YOU_DM_POLL_SECONDS=10`, while the default remains 65 seconds for installations with tighter history rate limits. Slack Retry-After is honored. Polling, generation and approval handling now run independently; review-channel lookups are cached. Timing columns record model duration and receive-to-card duration without recording extra message content.

To send approved replies as the owner in the original DM, add **chat:write to User Token Scopes**, reauthorize through the app's `/slack/install` flow, then set `VIRTUAL_YOU_DM_SEND_AS=user` and restart. Each draft snapshots its sending identity; older cards remain bot-authored. User-mode sends verify the owner/workspace and required scope, use the saved original DM, and fail closed rather than falling back to bot delivery. User events containing the owner's own messages never generate replies or post bot onboarding prompts into human conversations.

### All personal DMs

Set `VIRTUAL_YOU_DM_WATCH_RECIPIENT='*'` to route every incoming human one-to-one DM by sender. Existing personas and their reviewed styles are preserved. New senders are verified with the owner's Slack token, registered separately, and queued for persona creation from the owner's historical messages. Before a profile is ready/reviewed (including fewer than ten usable examples), reply cards explicitly show a neutral fallback. No other person's style or examples are used. A failed or unreviewed profile does not prevent a neutral approval-only reply. Use Refresh style after more examples become available, then review it in Home.

User `message.im` events are the primary input. The listener discovers accessible DMs every minute and polls one conversation per configured interval as bounded recovery. Recovery latency therefore grows with inbox size; keep user event subscriptions enabled. Old history from before inbox activation is excluded. Event/poll duplicates share message IDs. The current scope excludes group DMs, public/private channels, bot messages and the owner's own messages. All replies still require approval and retain the sender identity shown on the card. Set `VIRTUAL_YOU_DM_SEND_AS=user` after the owner has granted user `chat:write` for replies in the original DM.

macOS `scripts/macos_service.py install --mode oauth` starts the backend at login and restarts it after failure. The ngrok tunnel also needs a running supervisor, and the Mac must remain awake and online. These local services do not provide availability while the machine is asleep, logged out, or powered off; use an always-on server for that requirement.

**Personal-DM delivery update:** Conversational replies now always use the owner's user token and original DM. Bot delivery is no longer an option for these replies, regardless of an old `VIRTUAL_YOU_DM_SEND_AS` value. Already-posted legacy bot approval cards are blocked from sending; reject them and use a fresh draft. Unnotified drafts are upgraded before their approval card is posted. The bot remains the private review interface. Missing user `chat:write` permission causes an error, never fallback delivery as the bot. Work-report delivery is a separate feature.

## Member 4 voice and work questions

See [MEMBER4.md](../MEMBER4.md) for the new voice and evidence-checking path.
Install the `voice` or `voice-cloud` backend extra in this worker's environment,
configure its provider, and reauthorize the manifest's added bot `files:read`.
The owner can upload a memo in the bot DM and confirm the transcript in Home.
Reply-enabled recipients' mentions/DM work questions now create pending shared
work drafts or visible escalations. Event/shortcut/polling retries deduplicate.
These work answers use the bot destination shown on the approval card; the
personal-chat user-token route described above remains separate. Neither path
grants automatic permission to send. Discord inbound events are not added here.
