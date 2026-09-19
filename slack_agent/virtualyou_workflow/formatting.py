"""Readable Slack mrkdwn without activating model-written mentions or links."""

import html
import re


def slack_text(text):
    # Preserve code verbatim; normalize common Markdown only outside code spans.
    parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`)', str(text))
    for index in range(0, len(parts), 2):
        value = re.sub(r'\*\*([^*\n]+)\*\*', r'*\1*', parts[index])
        value = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', value, flags=re.M)
        value = re.sub(r'^[ \t]*[-*][ \t]+', '• ', value, flags=re.M)
        parts[index] = value
    # Escape Slack's control syntax (<@user>, <!channel>, <url|label>).
    return html.escape(''.join(parts), quote=False)


def formatted_section(text):
    return {'type': 'section', 'text': {'type': 'mrkdwn', 'text': slack_text(text), 'verbatim': True}}
