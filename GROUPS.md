# Group conversations

## Interaction and identity

Use a human message's **More actions → Ask a VirtualYou** shortcut. Select the
owner and confirm a question. Message shortcuts retain channel/message context;
custom slash commands cannot be invoked inside threads. App mentions do not
provide per-person bot aliases. This implementation deliberately uses only the
message shortcut for groups: no channel/MPIM message subscriptions or automatic
ambient-message responses are added.

The current installation hosts one configured owner. Choosing another owner is
rejected; hosting several owners in one installation requires a separate
installation/credential routing design. Replies use the verified **owner user
token**, with a visible **VirtualYou for [owner] · owner-approved/automatic** label,
and `thread_ts` set to the source thread root (or selected top-level message).
Private-DM behavior remains separate.

References verified during implementation:
- https://docs.slack.dev/interactivity/implementing-shortcuts/
- https://docs.slack.dev/interactivity/implementing-slash-commands/
- https://docs.slack.dev/reference/methods/conversations.replies/
- https://docs.slack.dev/reference/methods/chat.postMessage/

## Installation and scope

Apply `slack_agent/manifest.json` to the Slack app, preserving your real HTTPS
callback URLs, and reconnect the owner through VirtualYou Home. Added **user**
scopes: `channels:read`, `channels:history`, `groups:read`, `groups:history`,
`mpim:read`, `mpim:history`. Existing `users:read`, `chat:write` and bot `commands`
are used. User history access is required for channel thread retrieval. These
Slack grants are broader than an individual channel; application policy restricts
retrieval and delivery to explicitly enabled conversations.

In owner Home choose **Enable a conversation**. Select the conversation, indexed
projects, allowed work sources, and a reviewed group style. Explicitly enable it.
Default is approval required. No private-DM persona, examples, project scope, or
source scope is inherited. Shared/external conversations are rejected. The owner
and requester must both be current members. Changed membership requires renewed
audience review. Unknown/missing permissions fail closed.

For the initial trial: `#all-test`, project `virtualyou`, formal group style,
approval required. Other conversations remain disabled.

## Processing and automatic mode

The shortcut creates a durable owner/requester/conversation/thread-bound request.
Up to 60 thread messages are fetched and the most recent 15 sanitized human
messages are passed as **untrusted reference context**, never factual evidence.
Incomplete pagination routes to owner attention instead of silently guessing.
The existing scoped retrieval, grounded LLM, citation validation, and uncertainty
service prepare the answer. Group style presets are explicitly reviewed and
independent of personal-DM personas.

The owner can enable **Allow automatic replies as me** per conversation.
Automatic sends additionally require an evidence/commitment/conflict review by
the model and conservative text checks. Failure or uncertainty leaves a pending
owner-review draft. Decision questions and missing/stale evidence escalate.
Semantic model checks are fallible; citations are not proof that every claim is
correct. Keep automatic mode off for higher-risk audiences.

**Turn off automatic replies** writes synchronously, increments a revocation
epoch, and prevents queued generations/sends (including work waiting for an
executor thread) from claiming automatic delivery. Stale queued settings cannot
turn automatic mode back on. Already-dispatched Slack requests cannot be recalled.
Scope/style changes invalidate prior drafts; turning automatic mode off alone
still permits the owner to approve them. No enabling action sends old pending
drafts retroactively. Global drafting pause is also respected.

The global `VIRTUAL_YOU_LIVE_DELIVERY` switch still controls simulation. Simulated
responses never advance the group's last-shared-update watermark. Successful
status/progress updates advance it to the retrieval cutoff, not send time, so
activity recorded during generation remains eligible for the next summary.
“Progress since the last update” uses this group's watermark, never a DM's.

`group_policies`, `group_requests`, `group_intents`, and `group_audit` live in the
private backend SQLite database. Audit rows retain mode, owner/requester,
conversation/thread, policy revision, question, answer, supporting evidence,
dispatch claim, outcome, and Slack receipt. Duplicate submissions/sends are
suppressed. Unknown delivery outcomes are not retried automatically, including
after restart. Verify Slack before requesting another reply.

## Validation

Tests cover channel and MPIM routing, owner targeting, explicit activation,
project/source exclusion, no private-persona reuse, thread context, owner approval,
auto review, disabling during generation/final delivery checks, stale enable jobs,
membership and evidence changes, loop prevention, duplicate sends, restart
recovery, and group-specific progress watermarks. Existing backend/DM tests run
alongside the new tests. Automated Slack calls use fakes and send no real messages.

## Runtime requirement

Do not run the listener on SQLite 3.51.0 or 3.51.1: they have a concurrent WAL
connection-close deadlock, fixed in 3.51.2. The backend rejects these versions at
startup. Check the actual interpreter with
`python -c 'import sqlite3; print(sqlite3.sqlite_version)'`; installing a newer
Python package alone does not update the SQLite library linked to that interpreter.
The local service uses an isolated Python 3.13.14 runtime with SQLite 3.53.1.
Upstream fix: https://sqlite.org/releaselog/3_51_2.html
