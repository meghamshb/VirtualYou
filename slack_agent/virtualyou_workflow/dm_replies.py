"""Scoped RAG replies with owner approval and delivery as the owner."""
import asyncio
import hashlib
import json
import logging
import os
import time
from decimal import Decimal

from virtual_you.backend.errors import ServiceError
from virtual_you.ingest.redact import redact_text

from .history import RetryLater, slack_call
from .views import plain


class DMReplies:
    def __init__(self, coordinator, recipient, *, recover=True, allow_generic=False):
        self.c = coordinator
        self.recipient = recipient
        self.allow_generic = allow_generic
        self.next_poll = 0
        self.next_prepare = 0
        self.prepare_lock = asyncio.Lock()
        self.verified_token = None
        self.owner_channel = None
        self.poll_seconds = max(5, float(os.getenv('VIRTUAL_YOU_DM_POLL_SECONDS', '65')))
        # Personal-DM replies always return through the owner's account.
        # The bot token is used only for private approval cards.
        self.send_as = 'user'
        with self.c.backend.store.connection(write=True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS slack_dm_replies (
                id TEXT PRIMARY KEY, recipient TEXT NOT NULL, channel TEXT NOT NULL,
                source_ts TEXT NOT NULL, prompt TEXT NOT NULL, reply TEXT,
                state TEXT NOT NULL, card_channel TEXT, card_ts TEXT,
                UNIQUE(channel, source_ts))''')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(slack_dm_replies)')}
            for name, definition in [('send_as', "TEXT NOT NULL DEFAULT 'bot'"),
                                     ('received_at', 'REAL'), ('model_seconds', 'REAL'), ('ready_at', 'REAL'),
                                     ('style_source', "TEXT NOT NULL DEFAULT 'reviewed persona'"),
                                     ('grounding', "TEXT NOT NULL DEFAULT '{}'"),
                                     ('policy', "TEXT NOT NULL DEFAULT ''")]:
                if name not in columns:
                    db.execute(f'ALTER TABLE slack_dm_replies ADD COLUMN {name} {definition}')
            # Never replay an interrupted send: it may already have reached Slack.
            if recover:
                db.execute("UPDATE slack_dm_replies SET state='delivery_unknown' WHERE state='sending'")
                db.execute("UPDATE slack_dm_replies SET state='notification_unknown' WHERE state='notifying'")
        self.key = 'dm_watch:' + recipient
        if not self.c.backend.store.metadata(self.key):
            self.c.backend.store.set_metadata(self.key, {'oldest': str(time.time()), 'cursor': '', 'newest': '0'})
        activation_key = self.key + ':activated'
        if not self.c.backend.store.metadata(activation_key):
            self.c.backend.store.set_metadata(activation_key, {'at': str(time.time())})

    def receive_event(self, event, team_id):
        """The signed Bolt handler calls this; never accept arbitrary workspace events."""
        person = self.c.state.recipient(self.recipient)
        if team_id != self.c.config.team_id or event.get('channel') != person.get('human_channel'):
            return
        if event.get('channel_type') != 'im' or self.c.preferences().get('paused'):
            return
        activated = self.c.backend.store.metadata(self.key + ':activated')['at']
        if Decimal(event.get('ts', '0')) <= Decimal(activated):
            return
        with self.c.backend.store.connection(write=True) as db:
            self._insert(db, person['human_channel'], event)

    def _insert(self, db, channel, message):
        if message.get('user') != self.recipient or message.get('bot_id') or message.get('subtype'):
            return
        content = redact_text(message.get('text', '')).strip()[:4000]
        if not content or not message.get('ts'):
            return
        ts = message['ts']
        identity = hashlib.sha256((channel + ':' + ts).encode()).hexdigest()
        db.execute('INSERT OR IGNORE INTO slack_dm_replies(id,recipient,channel,source_ts,prompt,state,send_as,received_at) VALUES(?,?,?,?,?,?,?,?)',
                   (identity, self.recipient, channel, ts, content, 'queued', self.send_as, time.time()))

    def get(self, reply_id):
        with self.c.backend.store.connection() as db:
            row = db.execute('SELECT * FROM slack_dm_replies WHERE id=? AND recipient=?', (reply_id, self.recipient)).fetchone()
        if not row:
            raise ServiceError('reply_not_found', 'This reply is not in the selected conversation.')
        return dict(row)

    async def run(self):
        async def loop(operation):
            while True:
                try:
                    await operation()
                except Exception:
                    logging.getLogger(__name__).error('DM reply operation failed; retrying without replaying uncertain sends.')
                    await asyncio.sleep(5)
                await asyncio.sleep(0.25)
        async with asyncio.TaskGroup() as group:
            group.create_task(loop(self.poll))
            group.create_task(loop(self.prepare_one))

    async def poll(self):
        if time.monotonic() < self.next_poll or self.c.preferences().get('paused'):
            return
        self.next_poll = time.monotonic() + self.poll_seconds
        person = self.c.state.recipient(self.recipient)
        if not person.get('human_channel'):
            return
        if not self.allow_generic:
            profile = self.c.backend.store.get_persona(self.c.profile_id(self.recipient))
            if person.get('reviewed_version') != profile['version']:
                return
        token = self.c.credentials.user_token()
        client = self.c.client_factory(token=token, timeout=15, retry_handlers=[])
        if self.verified_token != token:
            identity = await asyncio.to_thread(slack_call, client.auth_test)
            if identity.get('user_id') != self.c.config.owner_id or identity.get('team_id') != self.c.config.team_id or identity.get('bot_id'):
                raise ServiceError('wrong_user_token', 'Reconnect the configured owner.')
            self.verified_token = token
        checkpoint = self.c.backend.store.metadata(self.key)
        try:
            response = await asyncio.to_thread(slack_call, client.conversations_history,
                channel=person['human_channel'], oldest=checkpoint['oldest'],
                cursor=checkpoint.get('cursor') or None, limit=15)
        except RetryLater as error:
            self.next_poll = time.monotonic() + max(65, error.seconds)
            return
        newest = Decimal(checkpoint.get('newest', '0'))
        with self.c.backend.store.connection(write=True) as db:
            for message in response.get('messages', []):
                ts = message.get('ts', '0')
                newest = max(newest, Decimal(ts))
                self._insert(db, person['human_channel'], message)
            cursor = response.get('response_metadata', {}).get('next_cursor', '')
            updated = {'oldest': checkpoint['oldest'] if cursor else str(max(newest, Decimal(checkpoint['oldest']))),
                       'cursor': cursor, 'newest': str(newest), 'last_checked': time.time()}
            db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', (self.key, json.dumps(updated)))

    def blocks(self, row, status=None):
        name = self.c.state.recipient(self.recipient).get('name', self.recipient)
        as_user = row.get('send_as') == 'user'
        delivery = ('Approve sends this exact reply as YOU in the original DM.' if as_user else
                    'Approve sends this exact reply from VirtualYou to this person’s bot DM.')
        blocks = [
            {'type': 'section', 'text': plain(f'Reply for {name} — approval required')},
            {'type': 'section', 'text': plain('Incoming: ' + row['prompt'][:2000])},
            {'type': 'section', 'text': plain(row['reply'] or 'Preparing reply…')},
            {'type': 'context', 'elements': [plain('Style: ' + row.get('style_source', 'reviewed persona'))]},
            {'type': 'context', 'elements': [plain(status or delivery + ' ' + ('Grounded in selected work evidence; review the quotes below.' if json.loads(row.get('grounding') or '{}').get('evidence') else 'No matching authorized work evidence.'))]},
        ]
        grounding = json.loads(row.get('grounding') or '{}')
        quotes = [citation['quote'] for paragraph in grounding.get('paragraphs', []) for citation in paragraph.get('citations', [])]
        if quotes:
            blocks.append({'type': 'section', 'text': plain('Source quotes (review for support):\n' + '\n'.join(quotes)[:2500])})
        if not status:
            blocks.append({'type': 'actions', 'elements': [
                {'type': 'button', 'text': plain('Approve & send as me' if as_user else 'Approve & send as bot'), 'action_id': 'vy_dm_approve', 'value': row['id']},
                {'type': 'button', 'text': plain('Reject'), 'action_id': 'vy_dm_reject', 'value': row['id']},
            ]})
        return blocks

    async def prepare_one(self):
        # Multiple inbox workers may select the same recipient; only one generates
        # and posts its approval card at a time.
        if self.prepare_lock.locked():
            return
        async with self.prepare_lock:
            await self._prepare_one()

    async def _prepare_one(self):
        if self.c.preferences().get('paused') or time.monotonic() < self.next_prepare:
            return
        self.next_prepare = time.monotonic() + 0.25
        with self.c.backend.store.connection() as db:
            row = db.execute("SELECT * FROM slack_dm_replies WHERE recipient=? AND state IN ('queued','generated') ORDER BY CAST(source_ts AS REAL) LIMIT 1", (self.recipient,)).fetchone()
        if not row:
            return
        row = dict(row)
        if row['send_as'] != 'user':
            row['send_as'] = 'user'
            with self.c.backend.store.connection(write=True) as db:
                db.execute("UPDATE slack_dm_replies SET send_as='user' WHERE id=?", (row['id'],))
        person = self.c.state.recipient(self.recipient)
        try:
            profile = self.c.backend.store.get_persona(self.c.profile_id(self.recipient))
        except ServiceError as error:
            if error.code != 'persona_not_found':
                raise
            profile = None
        limited_history = profile is not None and profile.get("seed_message_count") is not None and profile["seed_message_count"] < 10
        reviewed = profile and person.get('reviewed_version') == profile['version']
        if not reviewed:
            if not self.allow_generic:
                return
            profile = {'style': {'tone': 'Neutral, polite, concise. Do not infer personal preferences.'}}
        style_source = 'reviewed persona for this person' if reviewed else 'neutral fallback — create/review this person’s profile in Home'
        if not reviewed and limited_history:
            from virtual_you.backend.persona import PersonaService
            profile = {'style': PersonaService.formal_style().model_dump()}
            style_source = 'formal fallback — fewer than 10 outgoing messages; profile awaiting review'
        if row['state'] == 'queued':
            try:
                started = time.monotonic()
                scope = self.c.scope(person)
                # Keep the standard recent window for general progress; targeted
                # questions may retrieve older work within the same allowed audience.
                import re
                if not re.search(r'\b(progress|status|update|report|recent|today|yesterday)\b', row['prompt'], re.I):
                    scope = scope.model_copy(update={'since': None})
                outcome = await self.c.backend.assistant.prepare(
                    row['id'], question=row['prompt'], scope=scope, style=profile['style'],
                    recipient_id=self.c.profile_id(self.recipient),
                    context={'recipient': self.recipient, 'dm_reply_id': row['id'], 'channel': row['channel'], 'policy': self.c.policy_fingerprint(person)})
                if outcome['status'] != 'draft_ready':
                    with self.c.backend.store.connection(write=True) as db:
                        db.execute("UPDATE slack_dm_replies SET state='escalated' WHERE id=?", (row['id'],))
                    await asyncio.to_thread(self.c.publish_home)
                    return
                result = outcome['reply']
                row['reply'] = result['text']
                row['grounding'] = json.dumps(result)
                row['policy'] = self.c.policy_fingerprint(person)
                row['style_source'] = style_source
                with self.c.backend.store.connection(write=True) as db:
                    db.execute("UPDATE slack_dm_replies SET reply=?,state='generated',model_seconds=?,style_source=?,grounding=?,policy=? WHERE id=?", (row['reply'], time.monotonic()-started, style_source, row['grounding'], row['policy'], row['id']))
            except Exception:
                with self.c.backend.store.connection(write=True) as db:
                    db.execute("UPDATE slack_dm_replies SET state='generation_failed' WHERE id=?", (row['id'],))
                raise
        client = self.c.bot()
        if not self.owner_channel:
            destination = await asyncio.to_thread(slack_call, client.conversations_open, users=self.c.config.owner_id)
            self.owner_channel = destination['channel']['id']
        channel = self.owner_channel
        with self.c.backend.store.connection(write=True) as db:
            db.execute("UPDATE slack_dm_replies SET state='notifying',card_channel=? WHERE id=?", (channel, row['id']))
        try:
            sent = await asyncio.to_thread(slack_call, client.chat_postMessage, channel=channel,
                text='VirtualYou has a reply for your approval.', blocks=self.blocks(row), unfurl_links=False, unfurl_media=False)
        except Exception:
            with self.c.backend.store.connection(write=True) as db:
                db.execute("UPDATE slack_dm_replies SET state='notification_unknown' WHERE id=?", (row['id'],))
            raise
        with self.c.backend.store.connection(write=True) as db:
            db.execute("UPDATE slack_dm_replies SET state='pending',card_ts=?,ready_at=? WHERE id=?", (sent['ts'], time.time(), row['id']))

    async def decide(self, reply_id, approve):
        row = self.get(reply_id)
        if approve and self.c.preferences().get('paused'):
            raise ServiceError('workflow_paused', 'Resume drafting before sending.')
        if row['state'] != 'pending':
            return
        if approve and row.get('send_as') != 'user':
            raise ServiceError('outdated_bot_reply', 'This old draft would send as the bot. Reject it and request a fresh reply.')
        if approve and row.get('policy'):
            person = self.c.state.recipient(self.recipient)
            if row['policy'] != self.c.policy_fingerprint(person):
                raise ServiceError('audience_changed', 'Settings or style changed. Reject this draft and prepare a fresh reply.')
            self.c.backend.retrieval.validate_snapshot(
                json.loads(row['grounding']).get('evidence', []), self.c.scope(person))
            if self.c.backend.assistant.evidence_problem(row['prompt'], json.loads(row['grounding'])):
                raise ServiceError('evidence_expired', 'Prepare a fresh reply; its supporting evidence is no longer current.')
        user_client = None
        if approve and row.get('send_as') == 'user':
            from .user_delivery import verified_owner_client
            user_client = await verified_owner_client(self.c, self.recipient, row['channel'])
        with self.c.backend.store.connection(write=True) as db:
            updated = db.execute("UPDATE slack_dm_replies SET state=? WHERE id=? AND state='pending'",
                ('sending' if approve else 'rejected', reply_id)).rowcount
        if not updated:
            return  # Double-clicks and Slack retries cannot send twice.
        status = 'Rejected. Nothing sent.'
        if approve:
            try:
                client = user_client
                channel = row['channel']
                await asyncio.to_thread(slack_call, client.chat_postMessage, channel=channel,
                    text=row['reply'], mrkdwn=False, parse='none', unfurl_links=False, unfurl_media=False)
            except Exception:
                with self.c.backend.store.connection(write=True) as db:
                    db.execute("UPDATE slack_dm_replies SET state='delivery_unknown' WHERE id=?", (reply_id,))
                raise
            with self.c.backend.store.connection(write=True) as db:
                db.execute("UPDATE slack_dm_replies SET state='sent' WHERE id=?", (reply_id,))
            status = 'Sent as you in the original DM.'
        await asyncio.to_thread(slack_call, self.c.bot().chat_update, channel=row['card_channel'], ts=row['card_ts'],
            text=status, blocks=self.blocks(row, status))


def register_dm_actions(app, coordinator, event_key):
    def make_handler(approve):
        def handler(ack, body):
            ack()
            if coordinator.authorized(body) and coordinator.dm_replies:
                reply_id = body['actions'][0]['value']
                coordinator.dm_replies.get(reply_id)
                coordinator.state.enqueue('dm_decision', {'id': reply_id, 'approve': approve}, event_key(body, 'dm_decision'))
        return handler
    for action, approve in [('vy_dm_approve', True), ('vy_dm_reject', False)]:
        app.action(action)(make_handler(approve))
