"""Transactional expiring grants and encrypted provider credentials."""

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Vault:
    def __init__(self, path: Path, key: str):
        self.cipher = Fernet(key.encode())
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        self.lock = threading.RLock()
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS entries (kind TEXT, id TEXT, value TEXT, expires REAL, PRIMARY KEY(kind,id))"
            )
        path.chmod(0o600)

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            try:
                with db:
                    yield db
            finally:
                db.close()

    def put(self, kind, key, value, ttl=0):
        # Encrypt all payloads, including pending grants. Identifiers are hashes.
        sealed = self.cipher.encrypt(json.dumps(value).encode()).decode()
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO entries VALUES (?,?,?,?)",
                (kind, key, sealed, time.time() + ttl if ttl else 0),
            )

    def get(self, kind, key, *, consume=False):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value,expires FROM entries WHERE kind=? AND id=?", (kind, key)
            ).fetchone()
            if not row:
                return None
            if consume or (row[1] and row[1] < time.time()):
                db.execute("DELETE FROM entries WHERE kind=? AND id=?", (kind, key))
            if row[1] and row[1] < time.time():
                return None
            return json.loads(self.cipher.decrypt(row[0].encode()))

    def delete(self, kind, key):
        with self.db() as db:
            db.execute("DELETE FROM entries WHERE kind=? AND id=?", (kind, key))

    def keys(self, kind):
        with self.db() as db:
            return [x[0] for x in db.execute("SELECT id FROM entries WHERE kind=?", (kind,))]
