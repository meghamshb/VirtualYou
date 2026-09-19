# Slack message design

VirtualYou uses Slack's native Block Kit for private review cards. The reusable order is:

1. **Header:** the job to do, such as “Work update” or “Reply to Meghamsh”.
2. **Identity and state:** “VirtualYou · AI assistant” with “Review required”, “Delivered”, or “Delivery simulated · nothing sent”.
3. **Two compact fields:** recipient / delivery mode, or sender identity / destination.
4. **Divider and complete draft:** the actual text to review, separated from the app's controls.
5. **Context:** evidence date, source quotes, communication style and delivery explanation.
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

A personal reply is not presented as a bot-token message when it actually uses the owner's account. New personal-DM candidates get their assistance label **before** persistence and review. The delivery path does not append anything after approval. Editing a newly labelled candidate must retain its label and blank line; validation explains this instead of silently modifying the submitted text. Other body edits and explicit style learning continue to work. Existing unlabelled drafts keep their existing approval contract.

Report approval previews distinguish live delivery from simulation. All existing action IDs, revision payloads and destinations are preserved. The visual template does not add permission to send, change automatic mode, or bypass evidence validation.

## Implementation

- `slack_agent/virtualyou_workflow/formatting.py`: shared heading, fields, complete-body chunks, accessible fallback and candidate disclosure helpers.
- `views.py`: report review/status cards.
- `dm_replies.py`: personal-DM review cards and disclosure at candidate creation.
- `coordinator.py`: complete fallback on report notification posts and updates.
- `learning.py`: edit-form explanation and disclosure validation before queuing.
- `member4.py`: clearer “Needs your judgment” escalation summaries.

Do not truncate the outgoing draft for a prettier review card. The chunker respects Slack's 3,000-character section limit **after** escaping. Keep source quotes visually separate from the reply. Group Home controls are unchanged because the shared Home view has a separate, tight block budget.

## Verification

Local fake-client verification: 155 Slack tests passed, including delivery identity, exact approved text, rejection, edited reply handling, duplicate-send prevention, legacy disclosure behavior, complete long-text chunking, accessible fallback and unchanged action payloads. One existing Starlette deprecation warning remains. No real messages were posted to test this design. Native Slack appearance and screen-reader playback still need a live review-card check after the backend is restarted.

## Slack references

Use native Block Kit instead of emulating a Discord embed with an image. [Block Kit](https://docs.slack.dev/block-kit/) supports message layouts and documents screen-reader fallback behavior. [Header blocks](https://docs.slack.dev/reference/block-kit/blocks/header-block/) provide a concise title; [section blocks](https://docs.slack.dev/reference/block-kit/blocks/section-block/) provide the body and paired fields. The [message API](https://docs.slack.dev/reference/methods/chat.postMessage/) documents posting and message identity. The current implementation uses established header, section, context, divider and action blocks.
