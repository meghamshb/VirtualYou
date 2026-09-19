"""All personal DMs: durable event routing, isolated personas, bounded recovery polling."""
import asyncio
import hashlib
import json
import logging
import os
import time
from decimal import Decimal
from slack_sdk.errors import SlackApiError

from virtual_you.backend.errors import ServiceError
from virtual_you.ingest.redact import redact_text
from .dm_replies import DMReplies
from .history import slack_call, RetryLater


class DMInbox:
    all_personal_dms = True

    def __init__(self, coordinator):
        self.c = coordinator
        self.monitors = {}
        self.verified_token = None
        self.next_discovery = 0
        self.next_poll = 0
        self.poll_index = 0
        self.prepare_index = 0
        self.poll_seconds = max(10, float(os.getenv('VIRTUAL_YOU_DM_POLL_SECONDS', '65')))
        store = self.c.backend.store
        if not store.metadata('dm_inbox_activation'):
            store.set_metadata('dm_inbox_activation', {'at': str(time.time())})
        self.activated = store.metadata('dm_inbox_activation')['at']
        with store.connection(write=True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS slack_dm_inbox (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL,
                received_at REAL NOT NULL, error TEXT)''')
        for person in self.c.state.recipients():
            self.monitor(person['recipient'], recover=not self.monitors)

    def monitor(self, recipient, *, recover=False):
        if recipient not in self.monitors:
            monitor = DMReplies(self.c, recipient, recover=recover, allow_generic=True)
            # A new sender's triggering message predates creation of their monitor.
            monitor.key = 'dm_watch:' + recipient
            checkpoint = self.c.backend.store.metadata(monitor.key)
            if Decimal(checkpoint['oldest']) > Decimal(self.activated):
                checkpoint['oldest'] = self.activated
                self.c.backend.store.set_metadata(monitor.key, checkpoint)
            self.monitors[recipient] = monitor
        return self.monitors[recipient]

    def receive_event(self, event, team_id):
        if team_id != self.c.config.team_id or self.c.preferences().get('paused'):
            return
        if (event.get('channel_type') != 'im' or event.get('user') == self.c.config.owner_id
                or not event.get('user') or event.get('bot_id') or event.get('subtype')):
            return
        if Decimal(event.get('ts', '0')) <= Decimal(self.activated):
            return
        text = redact_text(event.get('text', '')).strip()[:4000]
        if not text or not event.get('channel'):
            return
        payload = {key: event[key] for key in ('user', 'channel', 'ts')}
        payload.update(text=text, channel_type='im')
        key = hashlib.sha256((payload['channel'] + ':' + payload['ts']).encode()).hexdigest()
        with self.c.backend.store.connection(write=True) as db:
            db.execute('INSERT OR IGNORE INTO slack_dm_inbox VALUES(?,?,?,?,NULL)',
                       (key, json.dumps(payload), 'queued', time.time()))

    async def client(self):
        token = self.c.credentials.user_token()
        client = self.c.client_factory(token=token, timeout=15, retry_handlers=[])
        if token != self.verified_token:
            identity = await asyncio.to_thread(slack_call, client.auth_test)
            if identity.get('user_id') != self.c.config.owner_id or identity.get('team_id') != self.c.config.team_id or identity.get('bot_id'):
                raise ServiceError('wrong_user_token', 'Reconnect the configured owner.')
            self.verified_token = token
        return client

    async def ensure_person(self, client, recipient, channel):
        # Verify with the OWNER token: a DM to the bot is not a human-to-human DM.
        try:
            info = await asyncio.to_thread(client.conversations_info, channel=channel)
        except SlackApiError as error:
            code = error.response.get('error')
            if code in {'channel_not_found', 'not_in_channel', 'access_denied'}:
                raise ServiceError('not_owner_dm', 'Not an accessible owner DM.') from None
            if code == 'ratelimited' or error.response.status_code == 429:
                raise RetryLater(error.response.headers.get('Retry-After', '60')) from None
            raise ServiceError('slack_access_error', 'Slack DM verification failed.') from None
        dm = info.get('channel', {})
        if not dm.get('is_im') or dm.get('user') != recipient:
            raise ServiceError('not_owner_dm', 'Not an owner-to-person DM.')
        try:
            person = self.c.state.recipient(recipient)
        except ServiceError as error:
            if error.code != 'recipient_not_selected':
                raise
            info = await asyncio.to_thread(slack_call, client.users_info, user=recipient)
            user = info['user']
            if user.get('is_bot') or user.get('deleted') or recipient == self.c.config.owner_id:
                raise ServiceError('not_human', 'Not an active human sender.')
            self.c.select(recipient, False, 'inbox-persona:' + recipient)
            person = self.c.state.recipient(recipient)
            person['name'] = redact_text(user.get('profile', {}).get('display_name') or user.get('real_name') or recipient)[:120]
        person['human_channel'] = channel
        self.c.state.save_recipient(person)
        return self.monitor(recipient)

    async def route_one(self):
        if self.c.preferences().get('paused'):
            return
        with self.c.backend.store.connection() as db:
            row = db.execute("SELECT * FROM slack_dm_inbox WHERE state='queued' ORDER BY received_at LIMIT 1").fetchone()
        if not row:
            return
        event = json.loads(row['payload'])
        try:
            person = self.c.state.recipient(event['user'])
        except ServiceError:
            person = {}
        try:
            if person.get('human_channel') == event['channel']:
                monitor = self.monitor(event['user'])
            else:
                monitor = await self.ensure_person(await self.client(), event['user'], event['channel'])
            with self.c.backend.store.connection(write=True) as db:
                monitor._insert(db, event['channel'], event)
                db.execute("UPDATE slack_dm_inbox SET state='routed',payload='{}' WHERE id=?", (row['id'],))
        except ServiceError as error:
            # Slack transient errors keep the event queued for recovery.
            if error.code not in {'not_owner_dm', 'not_human'}:
                raise
            with self.c.backend.store.connection(write=True) as db:
                db.execute("UPDATE slack_dm_inbox SET state='ignored',payload='{}',error=? WHERE id=?", (error.code, row['id']))

    async def discover(self):
        if self.c.preferences().get('paused') or time.monotonic() < self.next_discovery:
            return
        self.next_discovery = time.monotonic() + 60
        client = await self.client()
        cursor = (self.c.backend.store.metadata('dm_inbox_discovery') or {}).get('cursor')
        response = await asyncio.to_thread(slack_call, client.conversations_list,
            types='im', limit=200, cursor=cursor or None, exclude_archived=True)
        known = self.c.backend.store.metadata('dm_inbox_channels') or {}
        for channel in response.get('channels', []):
            if channel.get('is_im') and channel.get('user') != self.c.config.owner_id:
                known[channel['id']] = channel['user']
        self.c.backend.store.set_metadata('dm_inbox_channels', known)
        self.c.backend.store.set_metadata('dm_inbox_discovery', {
            'cursor': response.get('response_metadata', {}).get('next_cursor', ''), 'last_checked': time.time()})

    async def poll_one(self):
        if self.c.preferences().get('paused') or time.monotonic() < self.next_poll:
            return
        self.next_poll = time.monotonic() + self.poll_seconds
        channels = dict(self.c.backend.store.metadata('dm_inbox_channels') or {})
        for person in self.c.state.recipients():
            if person.get('human_channel'):
                channels[person['human_channel']] = person['recipient']
        if not channels:
            return
        channel, recipient = sorted(channels.items())[self.poll_index % len(channels)]
        self.poll_index += 1
        key = 'dm_inbox_poll:' + channel
        checkpoint = self.c.backend.store.metadata(key) or {'oldest': self.activated}
        response = await asyncio.to_thread(slack_call, (await self.client()).conversations_history,
            channel=channel, oldest=checkpoint['oldest'], cursor=checkpoint.get('cursor') or None, limit=15)
        newest = Decimal(checkpoint.get('newest', checkpoint['oldest']))
        for event in response.get('messages', []):
            newest = max(newest, Decimal(event.get('ts', '0')))
            # No history before activation and no other participant's persona.
            if event.get('user') == recipient:
                self.receive_event({**event, 'channel':channel, 'channel_type':'im'}, self.c.config.team_id)
        cursor = response.get('response_metadata', {}).get('next_cursor', '')
        self.c.backend.store.set_metadata(key, {'oldest': checkpoint['oldest'] if cursor else str(newest),
            'newest': str(newest), 'cursor': cursor, 'last_checked': time.time()})

    async def prepare_one(self):
        if not self.monitors:
            return
        with self.c.backend.store.connection() as db:
            pending = {r[0] for r in db.execute("SELECT DISTINCT recipient FROM slack_dm_replies WHERE state IN ('queued','generated')")}
        monitors = [m for key, m in self.monitors.items() if key in pending and not m.prepare_lock.locked()]
        if not monitors:
            return
        monitor = monitors[self.prepare_index % len(monitors)]
        self.prepare_index += 1
        await monitor.prepare_one()

    def get(self, reply_id):
        with self.c.backend.store.connection() as db:
            row = db.execute('SELECT recipient FROM slack_dm_replies WHERE id=?', (reply_id,)).fetchone()
        if not row:
            raise ServiceError('reply_not_found', 'Reply not found.')
        return self.monitor(row['recipient']).get(reply_id)

    async def decide(self, reply_id, approve, **kwargs):
        row = self.get(reply_id)
        await self.monitor(row['recipient']).decide(reply_id, approve, **kwargs)

    async def run(self):
        async def loop(operation):
            while True:
                delay = 0.25
                try:
                    await operation()
                except RetryLater as error:
                    delay = error.seconds
                except Exception:
                    logging.getLogger(__name__).error('Inbox operation failed; retrying without logging private content.')
                    delay = 15
                await asyncio.sleep(delay)
        async with asyncio.TaskGroup() as group:
            for operation in (self.discover, self.poll_one, self.route_one, self.prepare_one, self.prepare_one, self.prepare_one):
                group.create_task(loop(operation))
