"""Per-request async locks without an unbounded cache of completed requests."""

import asyncio
from contextlib import asynccontextmanager


class KeyedLocks:
    def __init__(self):
        self.entries = {}

    @asynccontextmanager
    async def hold(self, key):
        lock, users = self.entries.get(key, (asyncio.Lock(), 0))
        self.entries[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            _, users = self.entries[key]
            if users == 1:
                del self.entries[key]
            else:
                self.entries[key] = (lock, users - 1)
