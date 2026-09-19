"""Keep the existing backend alive in-process; no dashboard or second command."""

import asyncio
import threading
from contextlib import suppress

from virtual_you.backend.app import create_app
from virtual_you.backend.config import Settings

from .coordinator import Coordinator


class HeadlessRuntime:
    def __init__(self, config, credentials):
        self.config, self.credentials = config, credentials
        self.ready = threading.Event()
        self.thread = None
        self.error = None
        self.loop = None

    def start(self):
        self.thread = threading.Thread(
            target=self._thread_main, daemon=True, name="virtualyou-backend"
        )
        self.thread.start()
        if not self.ready.wait(90):
            raise RuntimeError("Backend startup timed out; check the ingestion feed connection.")
        if self.error:
            raise self.error
        return self.coordinator

    def _thread_main(self):
        try:
            asyncio.run(self._main())
        except Exception as error:
            self.error = error
            self.ready.set()

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self.stop_event = asyncio.Event()
        app = create_app(Settings.from_env())
        async with app.router.lifespan_context(app):
            self.coordinator = Coordinator(app.state, self.config, self.credentials)
            task = asyncio.create_task(self.coordinator.run())
            self.ready.set()
            try:
                # A worker failure fails the runtime, rather than silently stopping automation.
                stopper = asyncio.create_task(self.stop_event.wait())
                done, _ = await asyncio.wait([task, stopper], return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    await task
            finally:
                task.cancel()
                stopper.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    def stop(self):
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.stop_event.set)
        if self.thread:
            self.thread.join(timeout=20)
