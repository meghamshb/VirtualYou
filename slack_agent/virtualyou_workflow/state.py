import json
import time
from uuid import uuid4

from virtual_you.backend.errors import ServiceError


class SlackState:
    def __init__(self, store):
        self.store = store
        with store.connection(write=True) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS slack_recipients (
                    recipient TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS slack_jobs (
                    id TEXT PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL, available REAL NOT NULL,
                    error TEXT, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS slack_drafts (
                    draft_id TEXT PRIMARY KEY, recipient TEXT NOT NULL,
                    card_channel TEXT, card_ts TEXT, notification_state TEXT NOT NULL
                );
            """)
            # Generation/profile jobs are resumable. Never replay a delivery action after a crash.
            db.execute(
                "UPDATE slack_jobs SET state='failed',error='interrupted_action' WHERE state='running' AND kind='action'"
            )
            db.execute("UPDATE slack_jobs SET state='queued' WHERE state='running'")
            db.execute(
                "UPDATE slack_drafts SET notification_state='unknown' WHERE notification_state='sending'"
            )

    def recipient(self, recipient):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT payload FROM slack_recipients WHERE recipient=?", (recipient,)
            ).fetchone()
        if not row:
            raise ServiceError(
                "recipient_not_selected", "Select this person in VirtualYou first.", 404
            )
        return json.loads(row[0])

    def recipients(self):
        with self.store.connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT payload FROM slack_recipients ORDER BY recipient")
            ]

    def save_recipient(self, value):
        with self.store.connection(write=True) as db:
            db.execute(
                "INSERT OR REPLACE INTO slack_recipients VALUES (?,?)",
                (value["recipient"], json.dumps(value)),
            )

    def enqueue(self, kind, payload, dedupe=None):
        job_id = str(uuid4())
        with self.store.connection(write=True) as db:
            db.execute(
                "INSERT OR IGNORE INTO slack_jobs VALUES(?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    dedupe or job_id,
                    kind,
                    json.dumps(payload),
                    "queued",
                    time.time(),
                    None,
                    time.time(),
                ),
            )
        return job_id

    def claim(self, lane=None):
        clause = ""
        if lane == "voice":
            clause = " AND kind IN ('voice_upload','voice_confirm')"
        elif lane == "main":
            clause = " AND kind NOT IN ('voice_upload','voice_confirm')"
        with self.store.connection(write=True) as db:
            row = db.execute(
                "SELECT * FROM slack_jobs WHERE state='queued' AND available<=?" + clause + " ORDER BY created LIMIT 1",
                (time.time(),),
            ).fetchone()
            if not row:
                return None
            db.execute("UPDATE slack_jobs SET state='running' WHERE id=?", (row["id"],))
            return {**dict(row), "payload": json.loads(row["payload"])}

    def progress(self, job, payload):
        job["payload"] = payload
        with self.store.connection(write=True) as db:
            db.execute(
                "UPDATE slack_jobs SET payload=? WHERE id=?", (json.dumps(payload), job["id"])
            )

    def finish(self, job, *, error=None, retry_after=None):
        state = "queued" if retry_after is not None else ("failed" if error else "done")
        with self.store.connection(write=True) as db:
            db.execute(
                "UPDATE slack_jobs SET state=?,error=?,available=? WHERE id=?",
                (state, error, time.time() + (retry_after or 0), job["id"]),
            )
            if retry_after is None and "history" in job["payload"]:
                clean = {key: value for key, value in job["payload"].items() if key != "history"}
                db.execute(
                    "UPDATE slack_jobs SET payload=? WHERE id=?", (json.dumps(clean), job["id"])
                )

    def bind_draft(self, draft_id, recipient):
        with self.store.connection(write=True) as db:
            db.execute(
                "INSERT OR IGNORE INTO slack_drafts VALUES(?,?,NULL,NULL,'pending')",
                (draft_id, recipient),
            )

    def draft_link(self, draft_id):
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM slack_drafts WHERE draft_id=?", (draft_id,)).fetchone()
        if not row:
            raise ServiceError(
                "draft_not_owned", "This draft is not part of your Slack workflow.", 403
            )
        return dict(row)

    def cards(self):
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT d.payload FROM slack_drafts s JOIN drafts d ON s.draft_id=d.id ORDER BY d.created_at DESC LIMIT 5"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def open_draft(self, recipient):
        with self.store.connection() as db:
            return (
                db.execute(
                    """SELECT d.id FROM slack_drafts s JOIN drafts d ON d.id=s.draft_id
                WHERE s.recipient=? AND d.status IN ('pending','approved','delivering','delivery_failed','delivery_unknown') LIMIT 1""",
                    (recipient,),
                ).fetchone()
                is not None
            )

    def card_status(self, draft_id, status, channel=None, ts=None):
        with self.store.connection(write=True) as db:
            db.execute(
                "UPDATE slack_drafts SET notification_state=?,card_channel=COALESCE(?,card_channel),card_ts=COALESCE(?,card_ts) WHERE draft_id=?",
                (status, channel, ts, draft_id),
            )

    def latest_error(self):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT error FROM slack_jobs WHERE error IS NOT NULL ORDER BY created DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None
