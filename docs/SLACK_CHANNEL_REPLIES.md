# Replies when a colleague tags Leo

A colleague sends a real Slack mention, such as `@Leo What changed in the latest commit?`, in an enabled channel. VirtualYou queues a scoped answer. In approval mode Leo reviews it in **VirtualYou Home → Group conversations** and selects **Approve group reply**. The reply appears in that question's thread, using Leo's account and an explicit VirtualYou label.

If the question already belongs to a thread, the reply stays under the original thread parent. Events are deduplicated so retries do not create another answer. Merely mentioning the VirtualYou bot is a different event and does not activate this owner-specific channel flow.

## One-time local setup

1. The Slack installation must include user `chat:write`, `channels:read/history`, `groups:read/history`, `mpim:read/history`, and `users:read`, matching `slack_agent/manifest.json`. User event subscriptions must include `message.channels`, `message.groups`, and `message.mpim`; the callback and backend must be running.
2. Leo and the person asking must be current members. Shared/external, archived and incompletely verified conversations are rejected. A later membership change requires Leo to review the audience again.
3. In **VirtualYou Home → Enable a conversation**, choose the channel, approved project(s), sources, and group communication style. Check **Enable explicit questions in this conversation** and the audience/style review checkbox. Leave automatic replies unchecked for owner approval.
4. For live channel replies while work reports remain simulated, set `VIRTUAL_YOU_GROUP_LIVE_DELIVERY=true` in the private operator environment and restart the backend. Keep `VIRTUAL_YOU_LIVE_DELIVERY=false` if reports should stay simulated. The group flag controls transport only: it does not approve a draft or enable automatic mode.
5. A colleague tags Leo (not the bot), asking a specific question. Review and approve the resulting group draft in Home, then confirm one reply in the original thread.

`VIRTUAL_YOU_GROUP_LIVE_DELIVERY` is optional. When unset it inherits the existing general delivery setting; explicit `false` keeps channel replies simulated even when work reports are live. The group Home panel and approval button display simulation mode. Both actual answers and automatic-mode review-status notices use the group setting.

The configure UI queues `group_configure` with channel, projects, sources, style, enabled, automatic and expected policy revision/automatic epoch. The coordinator invokes `groups.configure` as the owner. There is no public unauthenticated “enable all channels” endpoint; do not inherit a colleague's private-DM project scope for a channel.

## Self-testing

Owner self-mentions are deliberately ignored by the normal mention listener, which also excludes bot messages and generated replies. Leo can use the explicit **Ask a VirtualYou** message shortcut to test a scoped thread himself. The separate `vy-test:` self-DM mechanism does not enable self-mentions in channels. For the actual mention-trigger acceptance test, another human should tag Leo.

## Acceptance boundary

Local fake-client tests cover same-thread delivery, approval, duplicate prevention, separate group/report delivery modes, group-mode review notices, membership and audience restrictions. Configuration alone does not prove Slack has delivered the event or that a real reply was received. At audit time the local installation had zero configured group policies and general delivery was set to simulation; enabling one reviewed channel is required before a mention can work.

References: Slack's [channel message event](https://docs.slack.dev/reference/events/message.channels/) carries `channel_type=channel` and requires channel history permission. An [app mention](https://docs.slack.dev/reference/events/app_mention/) targets the app identity. [chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/) uses `thread_ts` for a threaded reply and accepts user `chat:write` credentials.

Local setup on 2026-09-20: `#all-test` was enabled through the real Slack settings modal for project `virtualyou`, Git evidence and concise bullets. Automatic sending is off. The installation has the required user scopes; group transport is live while report delivery remains simulated. A fresh colleague mention and final owner-approved threaded delivery remain the live acceptance check.
