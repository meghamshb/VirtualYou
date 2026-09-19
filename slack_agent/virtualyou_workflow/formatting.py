"""Readable Slack mrkdwn without activating model-written mentions or links."""

import html
import json
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


def delivered_reply_blocks(text):
    """The same complete reply layout is previewed and delivered.

    This adds presentation, not a new factual summary. The assistance disclosure
    is already in the saved text; older unlabelled candidates stay undecorated.
    """
    prefix = ASSISTED_REPLY_LABEL + "\n\n"
    if not text.startswith(prefix):
        return formatted_sections(text)
    body = text[len(prefix):]
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "VirtualYou"}},
        {"type": "context", "elements": [
            {"type": "plain_text", "text": ASSISTED_REPLY_LABEL}
        ]},
        {"type": "divider"},
        *formatted_sections(body),
    ]


def source_quotes(grounding):
    """Deduplicate exact repeated citations while preserving their order."""
    return list(dict.fromkeys(
        citation['quote']
        for paragraph in grounding.get('paragraphs', [])
        for citation in paragraph.get('citations', [])
        if citation.get('quote')
    ))


def evidence_preview(quotes):
    count = len(quotes)
    excerpts = [" ".join(quote.split()) for quote in quotes[:2]]
    excerpts = [value[:157] + '…' if len(value) > 160 else value for value in excerpts]
    return {
        "type": "section",
        "text": {"type": "plain_text", "text":
            f"Source quotes · {count} unique excerpt{'s' if count != 1 else ''}\n"
            + '\n'.join('• ' + value for value in excerpts)
            + '\nOpen View evidence for the complete quotes.'},
    }


def evidence_modal(quotes, *, reply_id="", page=0):
    # The normal grounding schema has at most 50 quotes, but old source records
    # can be longer. Paginate every character instead of truncating at Slack's
    # per-section and 100-block modal limits.
    chunks = []
    for index, quote in enumerate(quotes, 1):
        labelled = f"{index}. {quote}"
        chunks.extend(labelled[i:i + 3000] for i in range(0, len(labelled), 3000))
    pages = max(1, (len(chunks) + 89) // 90)
    page = max(0, min(int(page), pages - 1))
    blocks = [
        {"type": "section", "text": {"type": "plain_text", "text":
            "Full source quotes for this reply. Check that they support the draft before approving."}},
        *[
            {"type": "section", "text": {"type": "plain_text", "text": chunk}}
            for chunk in chunks[page * 90:(page + 1) * 90]
        ],
    ]
    if pages > 1:
        blocks.append({"type": "context", "elements": [
            {"type": "plain_text", "text": f"Page {page + 1} of {pages} · all quotes retained"}
        ]})
        buttons = []
        for label, target in (("Previous", page - 1), ("Next", page + 1)):
            if 0 <= target < pages:
                buttons.append({"type": "button", "text": {"type": "plain_text", "text": label},
                                "action_id": f"vy_dm_evidence_{label.lower()}",
                                "value": json.dumps({"id": reply_id, "page": target})})
        blocks.append({"type": "actions", "elements": buttons})
    return {
        "type": "modal", "title": {"type": "plain_text", "text": "Reply evidence"},
        "close": {"type": "plain_text", "text": "Close"}, "blocks": blocks,
    }
