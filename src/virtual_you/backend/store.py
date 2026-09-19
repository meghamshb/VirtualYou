"""SQLite persistence. Every approval/state change is a short write transaction."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.reporting import utcnow


class Store:
    def __init__(self, path: Path):
        self.path = path
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported backend database version")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS activities (
                    id INTEGER PRIMARY KEY, session_id TEXT UNIQUE NOT NULL,
                    record_hash TEXT NOT NULL, ended_at TEXT NOT NULL,
                    payload TEXT NOT NULL, origin TEXT NOT NULL,
                    search_text TEXT NOT NULL, indexed_at TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS activity_search USING fts5(
                    search_text, tokenize='unicode61'
                );
                CREATE TABLE IF NOT EXISTS activity_projects (
                    session_id TEXT PRIMARY KEY, project_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS personas (
                    recipient_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS draft_revisions (
                    draft_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(draft_id, revision)
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, draft_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, action TEXT NOT NULL,
                    at TEXT NOT NULL, detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                PRAGMA user_version=1;
            """)
        path.chmod(0o600)

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def metadata(self, key):
        with self.connection() as db:
            row = db.execute("SELECT payload FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_metadata(self, key, payload):
        with self.connection(write=True) as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(payload)))

    def get_persona(self, recipient_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM personas WHERE recipient_id=?", (recipient_id,)
            ).fetchone()
        if not row:
            raise ServiceError("persona_not_found", "Create this recipient's persona first.", 404)
        return json.loads(row[0])

    def list_personas(self):
        with self.connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT payload FROM personas ORDER BY recipient_id")
            ]

    @staticmethod
    def load_draft(db, draft_id):
        row = db.execute("SELECT payload FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if not row:
            raise ServiceError("draft_not_found", "Draft not found.", 404)
        return json.loads(row[0])

    def get_draft(self, draft_id):
        with self.connection() as db:
            return self.load_draft(db, draft_id)

    @staticmethod
    def save_draft(db, draft, action, detail=""):
        payload = json.dumps(draft)
        db.execute(
            """INSERT INTO drafts VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            revision=excluded.revision,status=excluded.status,payload=excluded.payload""",
            (draft["id"], draft["revision"], draft["status"], payload, draft["created_at"]),
        )
        db.execute(
            "INSERT OR REPLACE INTO draft_revisions VALUES (?,?,?)",
            (draft["id"], draft["revision"], payload),
        )
        db.execute(
            "INSERT INTO audit(draft_id,revision,action,at,detail) VALUES(?,?,?,?,?)",
            (draft["id"], draft["revision"], action, utcnow(), detail),
        )

    def list_drafts(self, limit=50):
        with self.connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM drafts ORDER BY created_at DESC LIMIT ?", (limit,)
                )
            ]

    def audit(self, draft_id):
        self.get_draft(draft_id)
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM audit WHERE draft_id=? ORDER BY id", (draft_id,)
                )
            ]

    def recover_interrupted_deliveries(self):
        # One server worker is required. A prior process may have sent before dying.
        with self.connection(write=True) as db:
            for row in db.execute(
                "SELECT payload FROM drafts WHERE status='delivering'"
            ).fetchall():
                draft = json.loads(row[0])
                draft["status"] = "delivery_unknown"
                draft["receipt"] = {
                    "draft_id": draft["id"],
                    "revision": draft["revision"],
                    "status": "unknown",
                    "platform": draft["destination"]["platform"],
                    "target": draft["destination"]["target"],
                    "message_id": None,
                    "error_code": "process_interrupted",
                    "attempted_at": utcnow(),
                }
                self.save_draft(
                    db,
                    draft,
                    "delivery_unknown",
                    "Process interrupted; manual reconciliation required.",
                )
