from virtualyou_workflow.formatting import formatted_section, slack_text


def test_slack_formats_labels_lists_and_preserves_code():
    text = '**Changes**\n\n- Updated `a_b.py`\n- Keep `**literal**` intact\n\n## Tests\n2 passed'
    rendered = slack_text(text)
    assert '*Changes*\n\n• Updated `a_b.py`' in rendered
    assert '`**literal**`' in rendered
    assert '\n\n*Tests*\n' in rendered


def test_formatting_does_not_activate_mentions_or_custom_links():
    text = '*Update* <@U123> <!channel> <https://example.com|click> & x'
    rendered = slack_text(text)
    assert '<' not in rendered
    assert '&lt;@U123&gt;' in rendered
    assert '&amp; x' in rendered
    assert formatted_section(text)['text']['verbatim'] is True


def test_long_escaped_review_keeps_every_character_within_section_limits():
    import html

    from virtualyou_workflow.formatting import formatted_sections
    text = '<@U123> & ' * 1100
    blocks = formatted_sections(text)
    assert all(len(block['text']['text']) <= 3000 for block in blocks)
    assert html.unescape(''.join(b['text']['text'] for b in blocks)) == text
    assert all(b['text']['verbatim'] for b in blocks)


def test_card_fallback_includes_identity_body_evidence_and_actions_without_mentions():
    from virtualyou_workflow.formatting import card_fallback, card_fields, card_heading
    blocks = card_heading('Reply to colleague', 'Review required') + [
        card_fields(**{'Sends as': 'Your Slack account'}), formatted_section('Done <@U123>'),
        {'type': 'context', 'elements': [{'type': 'plain_text', 'text': 'Evidence <!channel>'}]},
        {'type': 'actions', 'elements': [{'type': 'button', 'text': {'type': 'plain_text', 'text': 'Approve'}}]},
    ]
    fallback = card_fallback(blocks)
    for value in ['VirtualYou', 'Review required', 'Your Slack account', 'Done', 'Evidence', 'Approve']:
        assert value in fallback
    assert '<@' not in fallback and '<!' not in fallback


def test_assistance_disclosure_is_idempotent_and_legacy_drafts_are_unchanged():
    import pytest
    from virtual_you.backend.errors import ServiceError

    from virtualyou_workflow.formatting import assisted_reply, validate_reply_disclosure
    candidate = assisted_reply('Done.')
    assert assisted_reply(candidate) == candidate
    validate_reply_disclosure({'reply': 'Old draft.'}, 'Human edit.')
    validate_reply_disclosure({'reply': candidate}, assisted_reply('Reworded.'))
    with pytest.raises(ServiceError, match='Keep the VirtualYou-assisted reply label'):
        validate_reply_disclosure({'reply': candidate}, 'Removed label.')
