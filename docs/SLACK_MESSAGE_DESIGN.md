# Slack message design

VirtualYou uses Slack's native Block Kit for private review cards and newly labelled outgoing personal-DM replies. The reusable order is:

1. **Header:** the job to do, such as “Work update” or “Reply to Meghamsh”.
2. **Identity and state:** “VirtualYou · AI assistant” with “Review required”, “Delivered”, or “Delivery simulated · nothing sent”.
3. **Two compact fields:** recipient / delivery mode, or sender identity / destination.
4. **Divider and complete draft:** the actual text to review, separated from the app's controls.
5. **Context:** evidence date, communication style and delivery explanation. Personal-DM review cards show at most two short source excerpts, a unique-quote count and **View evidence** for complete quotes.
6. **Actions:** one primary approval button and the existing edit, regenerate or reject controls.

The message body remains readable without external imagery. Slack's installed app icon and app identity supply the branding. State is expressed in words, not only color or emoji. Plain-text fallback contains the card's content and actions for notifications and screen readers. Untrusted names, source quotes and model text cannot activate Slack mentions through the presentation layer.

## Sender identity is part of the product contract

| Path | Slack identity | Disclosure |
| --- | --- | --- |
| Private review/status card | VirtualYou bot | Visible “VirtualYou · AI assistant” line |
| New generated personal DM reply | Owner's Slack account | “VirtualYou-assisted reply” is part of the saved candidate, visible before approval and delivered with the body |
| Existing personal DM draft | Its existing delivery identity | Existing saved text is preserved; no retroactive alteration |
| Group reply | Owner's Slack account | Existing VirtualYou group prefix remains unchanged |
| Bot-delivered work report | VirtualYou bot | Slack's app identity; the review card explicitly identifies the bot destination |

For new assisted personal-DM replies, the recipient sees a large **VirtualYou** header, the already-reviewed assistance label, a divider and the complete message body. This exact block sequence also appears in the owner review card. The top-level plain-text fallback remains the approved reply text, with Slack control syntax safely escaped. Styling does not add a factual summary or change wording. Existing unlabelled pending drafts keep plain delivery, and already-sent messages are not rewritten.

A personal reply is not presented as a bot-token message when it actually uses the owner's account. New personal-DM candidates get their assistance label **before** persistence and review. The delivery path does not append anything after approval. Editing a newly labelled candidate must retain its label and blank line; validation explains this instead of silently modifying the submitted text. Other body edits and explicit style learning continue to work. Existing unlabelled drafts keep their existing approval contract.

Report approval previews distinguish live delivery from simulation. All existing action IDs, revision payloads and destinations are preserved. The visual template does not add permission to send, change automatic mode, or bypass evidence validation.

## Implementation

- `slack_agent/virtualyou_workflow/formatting.py`: shared heading, fields, complete-body chunks, accessible fallback and candidate disclosure helpers.
- `views.py`: report review/status cards.
- `dm_replies.py`: personal-DM review cards, disclosure at candidate creation, the matching outgoing blocks and owner-only evidence modal actions.
- `coordinator.py`: complete fallback on report notification posts and updates.
- `learning.py`: edit-form explanation and disclosure validation before queuing.
- `member4.py`: clearer “Needs your judgment” escalation summaries.

Do not truncate the outgoing draft for a prettier review card. The chunker respects Slack's 3,000-character section limit **after** escaping. Keep source quotes visually separate from the reply. **View evidence** preserves all unique source quotes in a read-only modal; long evidence uses Previous/Next pages within Slack's block and text limits. The excerpt card is a summary, not a replacement for the full evidence. No evidence is sent to the colleague as part of this presentation change. Group Home controls are unchanged because the shared Home view has a separate, tight block budget.

## Verification

Local fake-client verification: the delivery-layout follow-up passed 159 Slack tests (including 44 focused formatting, DM and edit tests), including delivery identity, exact approved text, rejection, edited reply handling, duplicate-send prevention, legacy disclosure behavior, complete long-text chunking, accessible fallback and unchanged action payloads. One existing Starlette deprecation warning remains. The initial live recipient check showed that the old outbound path was still plain text. After the follow-up, a real self-DM test generated a review card, opened the full evidence modal, and delivered the approved reply with the new Block Kit layout. Native Slack appearance was inspected; screen-reader playback remains untested. The combined self-test, formatting and timestamp-recovery suite passed 195 tests.

## Slack references

Use native Block Kit instead of emulating a Discord embed with an image. [Block Kit](https://docs.slack.dev/block-kit/) supports message layouts and documents screen-reader fallback behavior. [Header blocks](https://docs.slack.dev/reference/block-kit/blocks/header-block/) provide a concise title; [section blocks](https://docs.slack.dev/reference/block-kit/blocks/section-block/) provide the body and paired fields. The [message API](https://docs.slack.dev/reference/methods/chat.postMessage/) documents posting and message identity. The current implementation uses established header, section, context, divider and action blocks.

## Reply content: explain the work

The conversational drafting prompt now starts with the meaning of a change, rather than a commit log or a list of implementation filenames. For a broad update it asks for a short answer, usually 50–100 words, with two to four outcome-led bullets when several changes matter. A focused question can get one or two direct sentences. Technical names, files and short commit references are included only when relevant to the question; exact SHA requests remain supported.

The prompt distinguishes a latest recorded restoration from an earlier removal and avoids presenting both as current. Selected evidence is not proof of branch ancestry, a complete history, live operation or deployment. Rationale must be explicitly supported, and test results must come from recorded test evidence. No automatic claim of “everything works” is allowed based on a commit.

Before the candidate is returned for review, bare full commit hashes are shortened to seven characters unless the user explicitly requests full identifiers. Exact source URLs and citation evidence are preserved. Default replies over 1,200 characters trigger the existing single repair attempt; detailed requests allow 2,400 characters. The repair keeps the same selected evidence and must preserve material qualifications; a second failure is surfaced for review rather than silently cutting the message. No content is changed after approval.

An illustrative rewrite from a test fixture:

> Restored the desktop app and connected its approvals to the backend.

This replaces a hash-led changelog sentence only when the source explicitly records that change. It is not a claim that the restored app passed a live test. Automated checks verify bounded rewrites, evidence retention, requested technical detail and no draft creation/delivery during generation. Naturalness and factual interpretation still require the next real self-DM review.

A bounded live content check on 2026-09-20 used the existing OpenAI configuration and a temporary database snapshot of the real `virtualyou` Git activity. No Slack message was sent and the live database was not modified. The first response was concise (66 words, 502 characters, four valid source references), but failed manual semantic review: it invented a reason for a revert and surfaced that older bookkeeping as a current update. The production prompt was tightened to prohibit inferred purposes from commit titles and to prioritize concrete recent product changes. The second and final allowed model call failed the reply JSON schema (`invalid_reply`); no candidate from that call was accepted. This is **not** a successful final live acceptance test. A fresh self-DM review is still required, and exact citation checks alone do not establish semantic correctness.

A subsequent actual self-DM test using the final prompt produced a concise latest-commit reply with three source excerpts and no full SHA in the outgoing body. The native review card was inspected and left pending for the owner. This verifies the live path to review, not automatic semantic correctness or a new delivery. The current combined Slack suite has 198 passing tests.
