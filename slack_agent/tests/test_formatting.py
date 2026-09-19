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


def test_delivered_blocks_brand_assisted_reply_without_rewriting_claims():
    from virtualyou_workflow.formatting import assisted_reply, delivered_reply_blocks, slack_text
    body = '**Progress**\n- Tests pass.\n\nDeployment is unknown. <@U123>'
    blocks = delivered_reply_blocks(assisted_reply(body))
    assert [block['type'] for block in blocks[:3]] == ['header', 'context', 'divider']
    assert blocks[0]['text']['text'] == 'VirtualYou'
    assert blocks[1]['elements'][0]['text'] == 'VirtualYou-assisted reply'
    assert ''.join(block['text']['text'] for block in blocks[3:]) == slack_text(body)
    assert '<@' not in str(blocks)
    assert 'Deployed' not in str(blocks)
    assert not any(b['type'] == 'header' for b in delivered_reply_blocks('Legacy body.'))


def test_evidence_is_compact_and_complete_quotes_remain_available():
    from virtualyou_workflow.formatting import evidence_modal, evidence_preview, source_quotes
    long_quote = 'A long source line about tests. ' * 40
    other_quote = 'No deployment evidence.'
    grounding = {'paragraphs': [
        {'citations': [{'quote': long_quote}, {'quote': other_quote}]},
        {'citations': [{'quote': long_quote}]},
    ]}
    quotes = source_quotes(grounding)
    assert quotes == [long_quote, other_quote]
    preview = evidence_preview(quotes)
    assert len(preview['text']['text']) < 500
    assert '2 unique excerpts' in preview['text']['text']
    assert 'View evidence' in preview['text']['text']
    modal = evidence_modal(quotes)
    assert modal['blocks'][1]['text']['text'] == '1. ' + long_quote
    assert modal['blocks'][2]['text']['text'] == '2. ' + other_quote
    assert 'submit' not in modal


def test_evidence_modal_paginates_all_quotes_without_truncation():
    import json

    from virtualyou_workflow.formatting import evidence_modal
    quotes = ['x' * 6100 for _ in range(50)]
    first = evidence_modal(quotes, reply_id='reply')
    second = evidence_modal(quotes, reply_id='reply', page=1)
    for modal in (first, second):
        assert len(modal['blocks']) <= 100
        assert all(len(block['text']['text']) <= 3000 for block in modal['blocks'] if block['type'] == 'section')
    restored = ''.join(block['text']['text'] for modal in (first, second) for block in modal['blocks'][1:] if block['type'] == 'section')
    assert restored == ''.join(f'{i}. {quote}' for i, quote in enumerate(quotes, 1))
    next_button = first['blocks'][-1]['elements'][0]
    assert json.loads(next_button['value']) == {'id': 'reply', 'page': 1}
