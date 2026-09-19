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
