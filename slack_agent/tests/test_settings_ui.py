import asyncio
import json
from types import SimpleNamespace

from virtualyou_workflow.listeners import register
from virtualyou_workflow.setup_views import setup_modal
from virtualyou_workflow.views import home_view

from .test_workflow_coordinator import draft, selected


def test_settings_retain_every_supported_source_and_do_not_claim_live_access(setup, monkeypatch):
    c, _, _ = setup
    sources = ['git', 'claude', 'cursor', 'codex', 'voice', 'github', 'jira', 'drive']
    c.backend.store.set_metadata('slack_preferences', {'sources': sources, 'paused': False})
    monkeypatch.setenv('VIRTUAL_YOU_GITHUB_REPO', 'owner/repo')
    monkeypatch.setenv('GITHUB_TOKEN', 'test-token')
    monkeypatch.setenv('VIRTUAL_YOU_MCP_GITHUB', 'true')
    view = setup_modal(c)
    element = next(b['element'] for b in view['blocks'] if b.get('block_id') == 'sources')
    assert {o['value'] for o in element['options']} == set(sources)
    assert {o['value'] for o in element['initial_options']} == set(sources)
    rendered = json.dumps(view)
    assert 'GitHub: configured' in rendered
    assert 'not a live connection test' in rendered
    assert 'test-token' not in rendered


def test_resolved_home_reports_are_compact_but_pending_reports_keep_full_review():
    common = {'revision': 1, 'destination': {}, 'text': 'Full report body.'}
    resolved = {**common, 'id': 'done', 'status': 'rejected'}
    pending = {**common, 'id': 'pending', 'status': 'pending', 'text': 'Pending full body.'}
    view = home_view([], [(resolved, 'Colleague'), (pending, 'Colleague')], connected=True, live=False, install_url='https://example.com')
    encoded = json.dumps(view)
    assert 'Full report body.' not in encoded and 'Pending full body.' in encoded
    assert 'vy_view_report' in encoded and 'vy_approve' in encoded
    assert 'Reconnect Slack' in encoded


def test_view_resolved_report_is_complete_owner_only_and_read_only(setup):
    class App:
        def __init__(self):
            self.handlers = {}
        def action(self, name):
            def save(fn):
                self.handlers[name] = fn
                return fn
            return save
        view = event = shortcut = action

    c, _, _ = setup
    selected(c)
    report = draft(c)
    c.state.enqueue('action', {'id': report['id'], 'revision': 1, 'action': 'reject'})
    asyncio.run(c.process_once())
    app = App()
    register(app, c)
    opened = []
    client = SimpleNamespace(views_open=lambda **kwargs: opened.append(kwargs))
    body = {'user': {'id': 'UOTHER'}, 'team': {'id': 'TTEAM'}, 'trigger_id': 'test',
            'actions': [{'value': json.dumps({'id': report['id'], 'revision': 1})}]}
    app.handlers['vy_view_report'](lambda: None, body, client)
    assert not opened
    body['user']['id'] = 'UOWNER'
    app.handlers['vy_view_report'](lambda: None, body, client)
    view = opened[0]['view']
    assert 'submit' not in view and all(b['type'] != 'actions' for b in view['blocks'])
    assert 'Rejected' in json.dumps(view)
    from virtualyou_workflow.formatting import formatted_sections
    for block in formatted_sections(report['text']):
        assert block in view['blocks']
