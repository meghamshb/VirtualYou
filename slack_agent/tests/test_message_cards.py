import json

import pytest

from virtualyou_workflow.formatting import card_fallback
from virtualyou_workflow.views import draft_blocks


@pytest.mark.parametrize('live,send_as,identity', [
    (False, 'user', 'Simulation · no message sent'),
    (True, 'user', 'Your Slack account'),
    (True, 'bot', 'VirtualYou bot'),
])
def test_review_cards_identify_delivery_without_changing_the_draft(live, send_as, identity):
    draft = {'id': 'draft-1', 'revision': 3, 'status': 'pending',
             'destination': {'send_as': send_as}, 'text': 'Tests passed. Deployment unknown.'}
    before = json.dumps(draft)
    blocks = draft_blocks(draft, 'Colleague', live)
    assert blocks[0]['type'] == 'header'
    assert identity in card_fallback(blocks)
    assert draft['text'] in card_fallback(blocks)
    assert json.dumps(draft) == before
    actions = [e for block in blocks if block['type'] == 'actions' for e in block['elements']]
    assert {a['action_id'] for a in actions} == {'vy_approve', 'vy_edit', 'vy_regenerate', 'vy_reject'}
    assert all(json.loads(a['value']) == {'id': 'draft-1', 'revision': 3} for a in actions)


def test_finished_card_is_status_not_stale_approval_prompt():
    draft = {'id': 'draft-1', 'revision': 3, 'status': 'simulated',
             'destination': {'send_as': 'bot'}, 'text': 'Tests passed.'}
    blocks = draft_blocks(draft, 'Colleague', False)
    fallback = card_fallback(blocks)
    assert 'Delivery simulated · nothing sent' in fallback
    assert 'Review required' not in fallback
    assert all(b['type'] != 'actions' for b in blocks)
