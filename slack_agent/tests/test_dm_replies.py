import asyncio
import json
from types import SimpleNamespace

import pytest
from virtual_you.backend.store import Store

from virtualyou_workflow.dm_replies import DMReplies, register_dm_actions


def supported_reply(kwargs):
    data = json.loads(kwargs['user'])
    e = next(item for item in data['evidence'] if item['field'] == 'end_state')
    return {'paragraphs': [{'text': e['text'], 'citations': [{'evidence_id': e['evidence_id']}]}], 'search_query': ''}


def make_monitor(tmp_path):
    store = Store(tmp_path / 'db.sqlite')
    person = {'name':'Colleague','human_channel':'DHUMAN','reviewed_version':1,'projects':['A']}
    store.get_persona = lambda key: {'version':1, 'style':{'tone':'friendly'}}
    calls = []
    class Slack:
        def auth_test(self): return {'user_id':'UOWNER','team_id':'TTEAM'}
        def conversations_history(self, **kwargs):
            return {'messages':[
                {'user':'UFRIEND','ts':'2000000000.1','text':'What is the current status? password=private123'},
                {'user':'UOWNER','ts':'2000000000.2','text':'owner message'},
                {'user':'UOTHER','ts':'2000000000.3','text':'unselected person'},
                {'user':'UFRIEND','ts':'2000000000.4','text':'bot','bot_id':'B1'},
            ]}
        def conversations_open(self, users): return {'channel':{'id': 'DBOT'+users}}
        def chat_postMessage(self, **kwargs):
            calls.append(kwargs)
            return {'ts':'2.0'}
        def chat_update(self, **kwargs): pass
    class Provider:
        async def generate(self, **kwargs):
            assert 'private123' not in kwargs['user']
            return supported_reply(kwargs)
    from virtual_you.backend.config import Settings
    from virtual_you.backend.drafting import DraftEngine
    from virtual_you.backend.retrieval import RetrievalService
    from virtual_you.contracts.reporting import RetrievalRequest
    retrieval = RetrievalService(store)
    from virtual_you.contracts.reporting import utcnow
    now = utcnow()
    retrieval.upsert({'session_id': 'routing-evidence', 'source': 'claude', 'redacted': True,
                      'end_state': 'Validation completed.', 'timestamp_range': {'started_at': now, 'ended_at': now}})
    retrieval.assign_project(['routing-evidence'], 'A')
    provider = Provider()
    slack = Slack()
    c = SimpleNamespace(
        backend=SimpleNamespace(store=store, persona=SimpleNamespace(provider=provider),
            retrieval=retrieval, engine=DraftEngine(Settings(),store,retrieval,provider)),
        state=SimpleNamespace(recipient=lambda r:person),
        config=SimpleNamespace(owner_id='UOWNER',team_id='TTEAM'),
        credentials=SimpleNamespace(user_token=lambda:'token', installation=lambda:SimpleNamespace(user_scopes=['chat:write'])),
        client_factory=lambda **kwargs:slack,
        scope=lambda person:RetrievalRequest(project_ids=person.get('projects', []),sources=['claude','cursor','codex']),
        policy_fingerprint=lambda person:json.dumps(person,sort_keys=True),
        preferences=lambda:{'paused':False}, profile_id=lambda r:r, bot=lambda:slack)
    from virtual_you.backend.assistant import AssistantService
    c.backend.assistant = AssistantService(Settings(), store, retrieval, SimpleNamespace(engine=c.backend.engine))
    c.publish_home = lambda: None
    return DMReplies(c,'UFRIEND'), calls


def test_only_selected_incoming_dm_and_no_send_before_approval(tmp_path):
    monitor, calls = make_monitor(tmp_path)
    async def run():
        await monitor.poll()
        monitor.next_poll=0
        await monitor.poll()
        with monitor.c.backend.store.connection() as db:
            rows = db.execute('select * from slack_dm_replies').fetchall()
        assert len(rows)==1
        assert 'private123' not in rows[0]['prompt']
        await monitor.prepare_one()
        assert len(calls)==1 and calls[0]['channel']=='DBOTUOWNER'
        await monitor.decide(rows[0]['id'], True)
        await monitor.decide(rows[0]['id'], True)
        assert len(calls)==2 and calls[1]['channel']=='DHUMAN'
        assert monitor.get(rows[0]['id'])['state']=='sent'
    asyncio.run(run())


def test_reject_never_sends_to_colleague(tmp_path):
    monitor, calls = make_monitor(tmp_path)
    async def run():
        await monitor.poll()
        await monitor.prepare_one()
        with monitor.c.backend.store.connection() as db:
            reply_id=db.execute('select id from slack_dm_replies').fetchone()[0]
        await monitor.decide(reply_id, False)
        await monitor.decide(reply_id, True)
        assert len(calls)==1
        assert monitor.get(reply_id)['state']=='rejected'
    asyncio.run(run())


def test_uncertain_delivery_not_replayed(tmp_path):
    monitor, calls = make_monitor(tmp_path)
    async def run():
        await monitor.poll()
        await monitor.prepare_one()
        with monitor.c.backend.store.connection() as db:
            reply_id=db.execute('select id from slack_dm_replies').fetchone()[0]
        slack=monitor.c.bot()
        def failure(**kwargs):
            calls.append(kwargs)
            raise TimeoutError()
        slack.chat_postMessage=failure
        with pytest.raises(TimeoutError):
            await monitor.decide(reply_id, True)
        await monitor.decide(reply_id, True)
        assert monitor.get(reply_id)['state']=='delivery_unknown'
        assert len(calls)==2
    asyncio.run(run())


def test_buttons_owner_only():
    handlers = {}
    jobs = []
    app=SimpleNamespace(action=lambda name:lambda fn:handlers.update({name:fn}), view=lambda name:lambda fn:handlers.update({name:fn}))
    c=SimpleNamespace(authorized=lambda body:body['user']=='owner',
        dm_replies=SimpleNamespace(get=lambda key:None),
        state=SimpleNamespace(enqueue=lambda *args:jobs.append(args)))
    register_dm_actions(app,c,lambda *args:'key')
    for user in ['stranger','owner']:
        handlers['vy_dm_approve'](lambda:None,{'user':user,'actions':[{'value':'draft'}]})
    assert len(jobs)==1 and jobs[0][1]['approve'] is True


def test_events_deduplicate_polling_and_filter_scope(tmp_path):
    monitor, calls = make_monitor(tmp_path)
    event = {'user':'UFRIEND','channel':'DHUMAN','channel_type':'im','ts':'2000000000.1','text':'Hi'}
    monitor.receive_event(event, 'WRONG')
    monitor.receive_event({**event,'channel':'OTHER'}, 'TTEAM')
    monitor.receive_event({**event,'user':'UOWNER'}, 'TTEAM')
    with monitor.c.backend.store.connection() as db:
        assert db.execute('select count(*) from slack_dm_replies').fetchone()[0]==0
    monitor.receive_event(event, 'TTEAM')
    monitor.receive_event(event, 'TTEAM')
    asyncio.run(monitor.poll())
    with monitor.c.backend.store.connection() as db:
        assert db.execute('select count(*) from slack_dm_replies').fetchone()[0]==1


def test_user_delivery_uses_original_dm_and_snapshotted_identity(tmp_path):
    monitor, calls=make_monitor(tmp_path)
    monitor.send_as='user'
    monitor.c.credentials.installation=lambda:SimpleNamespace(user_scopes=['chat:write'])
    async def run():
        await monitor.poll()
        monitor.send_as='bot'  # Settings changes must not change an existing draft.
        await monitor.prepare_one()
        with monitor.c.backend.store.connection() as db:
            reply_id=db.execute('select id from slack_dm_replies').fetchone()[0]
        assert 'Approve & send as me' in json.dumps(calls[0]['blocks'])
        await monitor.decide(reply_id, True)
        assert calls[-1]['channel']=='DHUMAN'
    asyncio.run(run())


def test_user_delivery_fails_closed_without_scope(tmp_path):
    monitor,calls=make_monitor(tmp_path)
    monitor.send_as='user'
    monitor.c.credentials.installation=lambda:SimpleNamespace(user_scopes=[])
    async def run():
        await monitor.poll()
        await monitor.prepare_one()
        with monitor.c.backend.store.connection() as db:
            reply_id=db.execute('select id from slack_dm_replies').fetchone()[0]
        from virtual_you.backend.errors import ServiceError
        with pytest.raises(ServiceError):
            await monitor.decide(reply_id, True)
        assert len(calls)==1 and monitor.get(reply_id)['state']=='pending'
    asyncio.run(run())


def test_legacy_bot_card_cannot_deliver(tmp_path):
    monitor,calls=make_monitor(tmp_path)
    async def run():
        await monitor.poll()
        await monitor.prepare_one()
        with monitor.c.backend.store.connection(write=True) as db:
            row=db.execute('select id from slack_dm_replies').fetchone()
            db.execute("update slack_dm_replies set send_as='bot' where id=?",(row[0],))
        from virtual_you.backend.errors import ServiceError
        with pytest.raises(ServiceError) as error:
            await monitor.decide(row[0], True)
        assert error.value.code=='outdated_bot_reply'
        assert len(calls)==1
    asyncio.run(run())


def test_user_send_uses_user_token_not_bot(tmp_path):
    monitor,calls=make_monitor(tmp_path)
    async def run():
        await monitor.poll()
        await monitor.prepare_one()
        with monitor.c.backend.store.connection() as db:
            key=db.execute('select id from slack_dm_replies').fetchone()[0]
        user_calls=[]
        class UserClient:
            def auth_test(self): return {'user_id':'UOWNER','team_id':'TTEAM'}
            def chat_postMessage(self, **kwargs):
                user_calls.append(kwargs)
                return {'ts': '3.0'}
        def factory(token,**kwargs):
            assert token=='token'
            return UserClient()
        monitor.c.client_factory=factory
        await monitor.decide(key,True)
        assert len(calls)==1  # Only the approval card used the bot.
        assert len(user_calls)==1 and user_calls[0]['channel']=='DHUMAN'
    asyncio.run(run())


def test_ingested_work_uses_correct_style_and_blocks_changed_evidence(tmp_path):
    from pathlib import Path

    from virtual_you.backend.errors import ServiceError
    from virtual_you.ingest.service import IngestionService
    monitor, calls = make_monitor(tmp_path)
    c=monitor.c
    source=Path(__file__).parents[2]/'tests/fixtures/claude_session.jsonl'
    record=IngestionService(data_directory=tmp_path/'raw-data',apply_git_overlay=False,latest_work_only=False).ingest_file('claude',source)
    from virtual_you.contracts.reporting import utcnow
    payload = record.model_dump(mode='json')
    payload['timestamp_range'] = {'started_at': utcnow(), 'ended_at': utcnow()}
    c.backend.retrieval.upsert(payload)
    c.backend.retrieval.assign_project([record.session_id],'virtualyou')
    c.state.recipient('UFRIEND')['projects']=['virtualyou']
    prompts=[]
    async def generate(**kwargs):
        data = json.loads(kwargs['user'])
        prompts.append(data)
        e=next(e for e in data['evidence'] if e['field']=='end_state')
        return {'paragraphs':[{'text':e['text'], 'citations':[{'evidence_id':e['evidence_id'],'quote':e['text']}]}]}
    c.backend.persona.provider.generate=generate
    async def run():
        with c.backend.store.connection(write=True) as db:
            monitor._insert(db,'DHUMAN',{'user':'UFRIEND','ts':'2000000001.0','text':'progress report'})
        await monitor.prepare_one()
        assert prompts[0]['style_only']=={'tone':'friendly'}
        assert len(calls)==1 and calls[0]['channel']=='DBOTUOWNER'
        assert 'Source quotes' in json.dumps(calls[0]['blocks'])
        with c.backend.store.connection() as db:
            reply_id=db.execute('SELECT id FROM slack_dm_replies').fetchone()[0]
        c.backend.retrieval.assign_project([record.session_id],'private')
        with pytest.raises(ServiceError) as error:
            await monitor.decide(reply_id,True)
        assert error.value.code=='evidence_changed'
        assert len(calls)==1
    asyncio.run(run())


def test_concurrent_workers_generate_one_approval_card(tmp_path):
    monitor,calls=make_monitor(tmp_path)
    async def run():
        await monitor.poll()
        await asyncio.gather(monitor.prepare_one(),monitor.prepare_one(),monitor.prepare_one())
        assert len(calls)==1
    asyncio.run(run())


def test_unreviewed_sparse_profile_uses_formal_fallback(tmp_path):
    monitor,calls=make_monitor(tmp_path)
    monitor.allow_generic=True
    monitor.c.state.recipient('UFRIEND')['reviewed_version']=None
    monitor.c.backend.store.get_persona=lambda key:{'version':1,'seed_message_count':3,'style':{'tone':'casual'}}
    seen=[]
    async def generate(**kwargs):
        seen.append(json.loads(kwargs['user'])['style_only'])
        return supported_reply(kwargs)
    monitor.c.backend.persona.provider.generate=generate
    async def run():
        await monitor.poll()
        await monitor.prepare_one()
        assert seen[0]['formality']=='formal'
        assert 'fewer than 10 outgoing messages' in json.dumps(calls[0]['blocks'])
    asyncio.run(run())
