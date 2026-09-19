import asyncio
import json
from types import SimpleNamespace

from virtual_you.backend.errors import ServiceError

from virtualyou_workflow.dm_inbox import DMInbox

from .test_dm_replies import make_monitor, supported_reply


def setup_inbox(tmp_path):
    old, calls = make_monitor(tmp_path)
    c = old.c
    people = {
        'UFRIEND': {'recipient':'UFRIEND','name':'First','human_channel':'DHUMAN','reviewed_version':1,'projects':['A']},
        'UTWO': {'recipient':'UTWO','name':'Second','human_channel':'DTWO','reviewed_version':1,'projects':['A']},
    }
    seeds = []
    model_inputs = []
    def person(key):
        if key not in people:
            raise ServiceError('recipient_not_selected', 'missing')
        return dict(people[key])
    def profile(key):
        if key not in {'UFRIEND', 'UTWO'}:
            raise ServiceError('persona_not_found', 'missing')
        return {'version':1,'style':{'tone':'formal' if key=='UFRIEND' else 'casual'}}
    def select(key, automatic, dedupe):
        seeds.append(key)
        people[key]={'recipient':key,'name':key,'status':'preparing','projects':['A']}
    c.state=SimpleNamespace(recipient=person, recipients=lambda:list(people.values()),
        save_recipient=lambda p:people.update({p['recipient']:dict(p)}))
    c.select=select
    c.backend.store.get_persona=profile
    async def generate(**kwargs):
        model_inputs.append(json.loads(kwargs['user']))
        return supported_reply(kwargs)
    c.backend.persona.provider.generate=generate
    slack=c.bot()
    slack.conversations_info=lambda channel:{'channel':{'is_im':True,'user':'UNEW'}}
    slack.users_info=lambda user:{'user':{'real_name':'New colleague','profile':{},'is_bot':False}}
    slack.conversations_list=lambda **kwargs:{'channels':[]}
    return DMInbox(c),people,seeds,model_inputs,calls


def event(user,channel,ts='2000000001.0'):
    return {'user':user,'channel':channel,'channel_type':'im','ts':ts,'text':'What is the current status?'}


def test_routing_uses_independent_personas_and_preserves_profiles(tmp_path):
    inbox,people,seeds,inputs,calls=setup_inbox(tmp_path)
    before=json.dumps(people,sort_keys=True)
    async def run():
        inbox.receive_event(event('UFRIEND','DHUMAN'),'TTEAM')
        inbox.receive_event(event('UTWO','DTWO'),'TTEAM')
        await inbox.route_one()
        await inbox.route_one()
        await inbox.prepare_one()
        await inbox.prepare_one()
        assert {x['style_only']['tone'] for x in inputs}=={'formal','casual'}
        assert all(x['channel']=='DBOTUOWNER' for x in calls)
        assert not seeds
        assert json.dumps(people,sort_keys=True)==before
    asyncio.run(run())


def test_new_sender_creates_profile_once_and_labels_fallback(tmp_path):
    inbox,people,seeds,inputs,calls=setup_inbox(tmp_path)
    async def run():
        inbox.receive_event(event('UNEW','DNEW'),'TTEAM')
        await inbox.route_one()
        inbox.receive_event(event('UNEW','DNEW'),'TTEAM')
        await inbox.route_one()
        await inbox.monitors['UNEW'].prepare_one()
        assert seeds==['UNEW']
        assert 'Neutral' in inputs[-1]['style_only']['tone']
        assert 'neutral fallback' in json.dumps(calls[-1]['blocks'])
        assert people['UFRIEND']['reviewed_version']==1
    asyncio.run(run())


def test_filters_wrong_workspace_group_self_bots_and_old_events(tmp_path):
    inbox,*_=setup_inbox(tmp_path)
    samples=[(event('UTWO','DTWO'),'WRONG'),
        ({**event('UTWO','DTWO'),'channel_type':'mpim'},'TTEAM'),
        (event('UOWNER','DTWO'),'TTEAM'),
        ({**event('UTWO','DTWO'),'bot_id':'B1'},'TTEAM'),
        ({**event('UTWO','DTWO'),'subtype':'message_changed'},'TTEAM'),
        (event('UTWO','DTWO','1.0'),'TTEAM')]
    for e, t in samples:
        inbox.receive_event(e, t)
    with inbox.c.backend.store.connection() as db:
        assert db.execute('select count(*) from slack_dm_inbox').fetchone()[0]==0


def test_new_monitor_does_not_reset_another_sending_reply(tmp_path):
    inbox,*_=setup_inbox(tmp_path)
    m=inbox.monitors['UFRIEND']
    with inbox.c.backend.store.connection(write=True) as db:
        m._insert(db,'DHUMAN',event('UFRIEND','DHUMAN'))
        db.execute("update slack_dm_replies set state='sending'")
    inbox.monitor('UOTHER')
    with inbox.c.backend.store.connection() as db:
        assert db.execute('select state from slack_dm_replies').fetchone()[0]=='sending'


def test_all_inbox_approval_routes_to_correct_person(tmp_path):
    inbox,people,seeds,inputs,calls=setup_inbox(tmp_path)
    async def run():
        inbox.receive_event(event('UTWO','DTWO'),'TTEAM')
        await inbox.route_one()
        await inbox.monitors['UTWO'].prepare_one()
        with inbox.c.backend.store.connection() as db:
            key=db.execute("select id from slack_dm_replies where recipient='UTWO'").fetchone()[0]
        await inbox.decide(key,True)
        await inbox.decide(key,True)
        assert len(calls)==2 and calls[-1]['channel']=='DTWO'
    asyncio.run(run())
