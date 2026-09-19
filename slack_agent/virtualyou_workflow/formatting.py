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


def formatted_sections(text):
    """Keep the full reviewed body within Slack's limit after escaping expands it."""
    remaining = str(text)
    blocks = []
    while remaining:
        size = min(len(remaining), 2800)
        while len(slack_text(remaining[:size])) > 3000:
            size = max(1, size // 2)
        if size < len(remaining):
            boundary = remaining.rfind("\n", 0, size)
            if boundary > size // 2:
                size = boundary + 1
        blocks.append(formatted_section(remaining[:size]))
        remaining = remaining[size:]
    return blocks


def card_heading(title, status, *, identity="VirtualYou · AI assistant"):
    return [
        {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
        {"type": "context", "elements": [
            {"type": "plain_text", "text": f"{identity}  •  {status}"[:2000]}
        ]},
    ]


def card_fields(**values):
    return {"type": "section", "fields": [
        {"type": "plain_text", "text": f"{label}\n{value}"[:2000]}
        for label, value in values.items()
    ]}


def card_fallback(blocks):
    """Mirror visible card content for notifications and screen readers.

    Never re-activate Slack mention/link syntax from plain-text blocks. This is
    only a review-card fallback, not a transformation of approved outbound text.
    """
    parts = []
    for block in blocks:
        objects = ([block["text"]] if "text" in block else []) + block.get("fields", [])
        for element in block.get("elements", []):
            if element.get("type") in {"plain_text", "mrkdwn"}:
                objects.append(element)
            elif element.get("type") == "button":
                objects.append(element["text"])
        for value in objects:
            text = value.get("text", "")
            parts.append(html.escape(text, quote=False) if value["type"] == "plain_text" else text)
    return "\n\n".join(parts)


ASSISTED_REPLY_LABEL = "VirtualYou-assisted reply"


def assisted_reply(text):
    """Disclose assistance before a new candidate is persisted and reviewed."""
    text = str(text)
    if text.startswith(ASSISTED_REPLY_LABEL + "\n\n"):
        return text
    return ASSISTED_REPLY_LABEL + "\n\n" + text


def validate_reply_disclosure(row, text):
    # Older drafts keep their existing approval contract. Never retrofit a label
    # after approval, nor silently alter the exact contents of an edit submission.
    if row.get("reply", "").startswith(ASSISTED_REPLY_LABEL + "\n\n") and not text.startswith(ASSISTED_REPLY_LABEL + "\n\n"):
        from virtual_you.backend.errors import ServiceError
        raise ServiceError(
            "disclosure_required",
            "Keep the VirtualYou-assisted reply label and blank line at the top so the recipient can identify AI assistance.",
            422,
        )
