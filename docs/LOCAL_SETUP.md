# Run leo-dev on your own Mac

**A browser tab opened by `npm run dev` is a UI preview.** It cannot read the
local backend key. Run Electron and connect it to a backend running on your own
Mac to see actual activity, reports and approvals. You do not need Leo's paths,
credentials, database or Slack installation.

## Prerequisites

- Git, Python **3.11 or newer**, and Node **22.12 or newer** (Node 24 LTS is a
  suitable choice). Use native Apple Silicon or Intel installers for your Mac.
- Check `python3 --version`, `node --version`, and `npm --version`. macOS's old
  system Python may be too old. The commands below do not assume Homebrew or a
  specifically named `python3.12` executable.
- Run commands from a local checkout of `leo-dev`. If you already have changes,
  save them before switching branches. No new branch is needed.

For a fresh checkout:

```sh
git clone --branch leo-dev https://github.com/meghamshb2006/MakeNoMistake.git
cd MakeNoMistake
```

## 1. Start an independent backend

In the first Terminal window, from the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[backend]'
python scripts/local_demo.py
```

Leave this Terminal running. The server prints its URL and the exact private
data folder to choose in Electron: `<your checkout>/.virtual-you/portable-demo`.

This runner creates synthetic activity, two recipient profiles, and one pending
report in **Approvals** so you can review it immediately. It uses the
offline demo drafting provider and simulated delivery. It does **not** read
`.env`, inherit integration credentials, import your editor history, or send
Slack messages. Its database is separate from other local backends. It seeds
the folder only once; rerunning keeps your demo data without generating another
sample report. Approving that report records a simulated delivery.

If port 8000 is busy, use `python scripts/local_demo.py --port 8001` and enter
8001 in Electron. Do not stop a teammate's existing service to free the port.

## 2. Launch Electron, then connect it

In a second Terminal window, from the same checkout:

```sh
cd desktop
npm ci
npm run desktop
```

1. Open **Settings → Developer / Advanced** in the Electron window. If an older
   build opens customer onboarding, choose **Developer / preview** first.
2. Enter **8000** (or the alternate port you selected).
3. Click **Choose data folder**. In the macOS folder picker, press
   **Command–Shift–G** and paste the full folder path printed by the backend.
   Choose the **directory**, not the `admin.key` file.
4. Confirm the header says **Local backend**, rather than **Preview**.

Electron reads the key in its main process and saves the connection using macOS
Keychain encryption. No key needs to be copied into chat, source code or Git.
The configured provider remains the **demo provider**: “Local backend” means a
real connection to your service, not paid AI generation or live Slack delivery.

The backend must remain running. A saved local connection can be reopened after
relaunch; if the backend is offline, the app shows offline status and disables
decisions until a successful refresh.

## 3. Check the backend without any accounts

Stop **your demo server** with Ctrl–C first; one worker may use a data directory.
From the activated environment at repository root:

```sh
python scripts/local_demo.py --check
python scripts/local_demo.py
```

The check exercises startup, synthetic activity indexing, drafting, approval
and simulated delivery. It prints pass/fail without displaying credentials.
It does not test microphone capture, remote transcription, a real model, Slack,
Jira or Drive. Those are separate integrations.

For developer tests, install `'.[backend,dev]'` instead of `'.[backend]'`.
`requirements.txt` alone installs ingestion/test dependencies, not the web
backend. `npm run dev` remains useful for interface work, but is sample-only.

## Optional real model and Slack

Keep the isolated demo runner for no-account checks. For your real backend,
follow [BACKEND.md](../BACKEND.md), create your own `.env` from `.env.example`,
and choose a **different private data directory**. Configure OpenAI there if
desired, then launch `virtual-you-server serve` from the activated environment.
Do not use `scripts/local_demo.py` for real provider configuration: it
deliberately ignores `.env` and forces simulation.

Slack requires an installed app, your own OAuth authorization and a callback
pointing to your running backend. Leo's temporary tunnel and installed token
files are not portable. Follow [the Slack workflow setup](../slack_agent/WORKFLOW.md).
When connecting Electron to the Slack server, use its port (normally 3000) and
its configured data directory. Jira/Drive authorization can be added afterward.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| The header says Preview | Open Electron with `npm run desktop`, then choose the local data folder. A browser preview cannot connect. |
| Backend is unreachable | Leave the first Terminal running; use the same port in Settings. |
| Folder does not contain a valid key | Run the backend first, then select its printed data directory. Hidden folders can be opened with Command–Shift–G. |
| `python3.12: command not found` | Use installed Python 3.11+ through `python3`; check its version. |
| `No module named fastapi` or `dotenv` | Activate `.venv`, then `python -m pip install -e '.[backend]'`. |
| Vite reports an unsupported Node version | Install Node 22.12+; open a new Terminal and rerun `npm ci`. |
| “Only one backend worker” | Stop your other server using the same data directory, or select a separate `--data-dir`. |
| Secure system storage is unavailable | Unlock the macOS login Keychain and reopen Electron. Browser preview cannot replace encrypted local storage. |

The current backend uses a Unix file lock. This guide targets macOS; Windows
native backend startup is not claimed as supported by this check.

Verified on 2026-09-20 using an exported source tree with no existing virtual
environment, `node_modules`, `.env`, tokens or database: a fresh Python 3.13
environment installed the backend extra, seeded synthetic activity, passed the
local draft/approval/simulation check, and served the authenticated HTTP API.
A fresh `npm ci` and Electron production build also passed on macOS Apple
Silicon with Node 26. Intel-specific execution and native Keychain/folder-picker
interaction on another teammate's Mac still need their local check.
