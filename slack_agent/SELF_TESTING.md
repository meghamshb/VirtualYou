# Test your VirtualYou in your own Slack DM

Self-testing is optional and off by default. It runs alongside the normal selected
colleague or all-DM listener. It does not create a colleague profile for yourself,
change another person's settings, or enable automatic sending.

Set these values in the private local operator environment, then restart the Slack
backend:

```dotenv
VIRTUAL_YOU_SELF_TEST_ENABLED=true
VIRTUAL_YOU_SELF_TEST_CHANNEL=D_YOUR_OWN_DM_ID
VIRTUAL_YOU_SELF_TEST_PROJECTS=your-project-id
```

The channel must be your **own** Slack DM (the conversation marked **you**), not
the VirtualYou bot DM or a teammate DM. Use its actual Slack channel ID beginning
with `D`, without underscores. Project IDs are comma-separated and must already
have activity. Self-testing also obeys the existing enabled work-source selection
and the Slack workflow pause switch.

In your own DM, send this as an ordinary message:

```text
vy-test: What is the current status?
```

The backend verifies your user-token identity, workspace and self-DM ownership.
It removes the prefix and passes the question through the existing scoped evidence
and uncertainty checks. A supported answer appears as an approval card in your
**VirtualYou bot DM**. Review the answer and evidence there, then click **Approve &
send as me**. The exact reviewed reply appears back in your own DM. You can reject
the card or use the normal explicit edit-and-send action. Self-tests do not offer
persona-learning buttons.

Ordinary self-notes, messages you send to teammates, bot/app messages, old messages
and VirtualYou's own answers do not trigger tests. The prefix is case-sensitive.
The transport also accepts `/vy-test <question>` as plain received text, but Slack
usually intercepts a leading slash as a slash command; **use `vy-test:` in the UI**.
No additional slash-command manifest configuration is required.

Expected checks:

- A factual question uses only the configured projects and enabled sources.
- A question such as `vy-test: Should we change the rollout scope?` escalates for
  human judgment instead of inventing a decision.
- An approval is required for each outgoing test answer; duplicate clicks/events
  do not send duplicates, and the answer does not trigger another reply.
- Pausing the Slack workflow stops test processing and approval delivery too.

To turn it off, set `VIRTUAL_YOU_SELF_TEST_ENABLED=false` and restart. Re-enabling
or changing the test configuration starts a new activation window and invalidates
older queued/generated/pending test replies. Restarting with the same enabled
configuration preserves event recovery. Interrupted sends remain uncertain and
are not replayed.

This validates the self-test route and the shared draft/approval/delivery code.
A real teammate DM is still a separate acceptance check for their persona and
audience configuration. OAuth credentials, installation state and source data stay
in the existing private local configuration; none belong in Git.
