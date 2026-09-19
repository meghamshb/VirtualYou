from __future__ import annotations

import asyncio
import fcntl
import hmac
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from virtual_you.backend.assistant import AssistantService
from virtual_you.backend.config import Settings
from virtual_you.backend.delivery import DeliveryGateway
from virtual_you.backend.drafting import DraftEngine
from virtual_you.backend.errors import ServiceError
from virtual_you.backend.heartbeat import Heartbeat
from virtual_you.backend.persona import PersonaService
from virtual_you.backend.providers import make_provider
from virtual_you.backend.retrieval import RetrievalService
from virtual_you.backend.store import Store
from virtual_you.backend.voice import MAX_AUDIO_BYTES, VoiceService, make_transcriber
from virtual_you.backend.workflow import Workflow
from virtual_you.contracts.assistant import (
    QuestionRequest,
    ResolveEscalation,
    VoiceConfirm,
    VoiceEdit,
)
from virtual_you.contracts.reporting import (
    ApprovalDecision,
    DraftRequest,
    EditRequest,
    PersonaSeed,
    ReconcileDelivery,
    RetrievalRequest,
    RevisionRequest,
)
from virtual_you.ingest.errors import IngestionError
from virtual_you.mcp.oauth import attach_oauth_routes


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            chunks.append(chunk)
            size += len(chunk)
            limit = MAX_AUDIO_BYTES if scope.get("path") == "/api/voice" else 1_000_000
            if size > limit:
                response = JSONResponse(
                    {
                        "error": {
                            "code": "request_too_large",
                            "message": "Request exceeds the upload size limit.",
                        }
                    },
                    status_code=413,
                )
                return await response(scope, receive, send)
            if not message.get("more_body"):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(settings=None, *, provider=None, transport=None, transcriber=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        settings.prepare()
        descriptor = os.open(settings.data_dir / "server.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(
                    "Only one backend worker may use this data directory."
                ) from error
            store = Store(settings.data_dir / "backend.sqlite3")
            store.recover_interrupted_deliveries()
            async with httpx.AsyncClient(
                timeout=settings.request_timeout, transport=transport, follow_redirects=False
            ) as client:
                retrieval = RetrievalService(store)
                model = provider or make_provider(settings, client)
                engine = DraftEngine(settings, store, retrieval, model)
                heartbeat = Heartbeat(settings, store, retrieval, client)
                app.state.settings, app.state.store = settings, store
                app.state.retrieval, app.state.engine = retrieval, engine
                app.state.persona = PersonaService(settings, store, model)
                app.state.workflow = Workflow(store, engine, DeliveryGateway(settings, client))
                app.state.assistant = AssistantService(
                    settings, store, retrieval, app.state.workflow
                )
                app.state.workflow.guards.append(app.state.assistant.guard)
                app.state.voice = VoiceService(
                    settings,
                    store,
                    retrieval,
                    app.state.workflow,
                    transcriber or make_transcriber(settings),
                )
                app.state.heartbeat = heartbeat
                await heartbeat.refresh()

                # Refresh already ran; avoid a duplicate startup scan.
                async def periodic():
                    await asyncio.sleep(settings.heartbeat_seconds)
                    await heartbeat.run()

                task = asyncio.create_task(periodic()) if settings.heartbeat_enabled else None
                try:
                    yield
                finally:
                    if task:
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
        finally:
            os.close(descriptor)

    app = FastAPI(
        title="Virtual You",
        version="0.2.0",
        lifespan=lifespan,
        description="Single-owner backend. All /api routes require the local admin key. Drafting never sends messages.",
    )
    app.add_middleware(BodyLimit)
    bearer = HTTPBearer(auto_error=False)

    def owner(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
        if not credentials or not hmac.compare_digest(credentials.credentials, settings.api_key):
            raise HTTPException(
                401, "A valid backend API key is required.", headers={"WWW-Authenticate": "Bearer"}
            )

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        return JSONResponse(
            {"error": {"code": error.code, "message": error.message}}, status_code=error.status
        )

    @app.exception_handler(IngestionError)
    async def ingestion_error(request, error):
        return JSONResponse(
            {
                "error": {
                    "code": "unsafe_input",
                    "message": "Input failed the ingestion safety check.",
                }
            },
            status_code=422,
        )

    @app.exception_handler(ValidationError)
    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Pydantic normally echoes input values; exclude them and custom exception contexts.
        fields = [{"location": list(item["loc"]), "type": item["type"]} for item in error.errors()]
        return JSONResponse(
            {
                "error": {
                    "code": "invalid_input",
                    "message": "Input does not match the contract.",
                    "fields": fields,
                }
            },
            status_code=422,
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path in {"/", "/review"} or request.url.path.startswith("/static/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        return response

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    attach_oauth_routes(app, settings.data_dir)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/review", include_in_schema=False)
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    api = APIRouter(prefix="/api", dependencies=[Depends(owner)])

    @api.get("/status")
    def status():
        return {
            "provider": app.state.engine.provider.name,
            "delivery_mode": "live" if settings.live_delivery else "simulation",
            "heartbeat": app.state.store.metadata("heartbeat"),
            "activity": app.state.retrieval.stats(),
            "projects": app.state.retrieval.project_choices(),
            "voice": {
                "provider": settings.voice_provider,
                "max_seconds": 180,
                "max_bytes": MAX_AUDIO_BYTES,
                "uploads_audio_to_provider": settings.voice_provider == "elevenlabs",
            },
            "destinations": {
                "slack": list(settings.slack_channels),
                "discord": ["default"] if settings.discord_webhook_url else [],
            },
        }

    @api.post("/refresh")
    async def refresh():
        return await app.state.heartbeat.refresh()

    @api.post("/activities", status_code=201)
    def import_activity(payload: dict):
        changed = app.state.retrieval.upsert(payload)
        return {"changed": changed}

    @api.post("/retrieval/search")
    def search(request: RetrievalRequest):
        return {"matches": app.state.retrieval.search(request)}

    @api.get("/personas")
    def personas():
        return app.state.store.list_personas()

    @api.post("/personas", status_code=201)
    async def create_persona(request: PersonaSeed):
        return await app.state.persona.create(request)

    @api.get("/personas/{recipient_id}")
    def persona(recipient_id: str):
        return app.state.store.get_persona(recipient_id)

    @api.get("/personas/{recipient_id}/soul", response_class=PlainTextResponse)
    def soul(recipient_id: str):
        return app.state.store.get_persona(recipient_id)["soul_md"]

    @api.get("/drafts")
    def drafts():
        return app.state.store.list_drafts()

    @api.post("/drafts", status_code=201)
    async def create_draft(request: DraftRequest):
        return await app.state.workflow.create(request)

    @api.get("/drafts/{draft_id}")
    def draft(draft_id: str):
        return app.state.store.get_draft(draft_id)

    @api.get("/drafts/{draft_id}/audit")
    def audit(draft_id: str):
        return app.state.store.audit(draft_id)

    @api.post("/drafts/{draft_id}/edit")
    def edit(draft_id: str, request: EditRequest):
        return app.state.workflow.edit(draft_id, request)

    @api.post("/drafts/{draft_id}/regenerate")
    async def regenerate(draft_id: str, request: RevisionRequest):
        return await app.state.workflow.regenerate(draft_id, request)

    @api.post("/drafts/{draft_id}/decision")
    def decision(draft_id: str, request: ApprovalDecision):
        return app.state.workflow.decide(draft_id, request)

    @api.post("/drafts/{draft_id}/deliver")
    async def deliver(draft_id: str, request: RevisionRequest):
        return await app.state.workflow.deliver(draft_id, request)

    @api.post("/drafts/{draft_id}/reconcile")
    def reconcile(draft_id: str, request: ReconcileDelivery):
        return app.state.workflow.reconcile(draft_id, request)

    @api.post("/assistant/questions")
    async def ask_question(request: QuestionRequest):
        return await app.state.assistant.ask(request)

    @api.get("/assistant/requests")
    def questions():
        return app.state.assistant.list_requests()

    @api.post("/assistant/requests/{request_id}/resolve")
    def resolve_question(request_id: str, request: ResolveEscalation):
        return app.state.assistant.resolve(request_id, request.note)

    @api.post("/voice", status_code=201)
    async def upload_voice(
        request: Request,
        request_id: str = Query(min_length=1, max_length=160, pattern=r"^[\w.:-]+$"),
    ):
        return await app.state.voice.upload(await request.body(), request_id)

    @api.get("/voice")
    def voice_notes():
        return app.state.voice.list_notes()

    @api.get("/voice/targets")
    def voice_targets():
        return getattr(app.state, "voice_targets", lambda: [])()

    @api.get("/voice/{note_id}")
    def voice_note(note_id: str):
        return app.state.voice.get(note_id)

    @api.post("/voice/{note_id}/edit")
    def edit_voice(note_id: str, request: VoiceEdit):
        return app.state.voice.edit(note_id, request)

    @api.post("/voice/{note_id}/confirm")
    async def confirm_voice(note_id: str, request: VoiceConfirm):
        return await app.state.voice.confirm(note_id, request)

    app.include_router(api)
    return app
