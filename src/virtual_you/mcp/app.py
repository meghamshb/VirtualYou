"""Starlette app: ack GitHub webhooks then apply observations."""

from typing import Optional

from virtual_you.mcp.observations import ObservationStore
from virtual_you.mcp.webhooks import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    apply_webhook,
    decode_payload,
    header_value,
    verify_signature,
)

try:
    from starlette.applications import Starlette
    from starlette.background import BackgroundTask
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route
except ImportError:
    Starlette = None
    BackgroundTask = None
    Request = None
    JSONResponse = None
    Response = None
    Route = None


def create_app(
    store: ObservationStore,
    secret: str,
) -> "Starlette":
    if Starlette is None:
        raise RuntimeError("Install the github extra to serve webhooks (starlette).")

    async def webhook(request: Request) -> Response:
        body = await request.body()
        signature = header_value(dict(request.headers), SIGNATURE_HEADER)
        if not verify_signature(secret, body, signature):
            return Response(status_code=401)
        event = header_value(dict(request.headers), EVENT_HEADER)
        delivery = header_value(dict(request.headers), DELIVERY_HEADER)
        try:
            payload = decode_payload(body)
        except ValueError:
            return Response(status_code=400)
        task = BackgroundTask(
            apply_webhook,
            store,
            event=event,
            delivery_id=delivery,
            payload=payload,
        )
        return JSONResponse({"accepted": True}, status_code=202, background=task)

    return Starlette(routes=[Route("/github/webhook", webhook, methods=["POST"])])
