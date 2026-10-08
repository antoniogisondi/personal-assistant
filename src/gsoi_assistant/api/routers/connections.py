from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from gsoi_assistant.api.deps import ContainerDep, UserDep
from gsoi_assistant.api.schemas import ConnectionOut, ConnectStartOut
from gsoi_assistant.connectors.google.auth import PROVIDER, GoogleAuth
from gsoi_assistant.core.errors import GsoiError

router = APIRouter(prefix="/v1/connections", tags=["connections"])


def _google(container: ContainerDep) -> GoogleAuth:
    if container.google is None:
        raise HTTPException(
            503,
            "Google is not configured: set GSOI_GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET "
            "and GSOI_MASTER_KEY in .env",
        )
    return container.google


@router.get("", response_model=list[ConnectionOut])
async def list_connections(container: ContainerDep, user_id: UserDep) -> list[ConnectionOut]:
    if container.google is None:
        return [
            ConnectionOut(provider=PROVIDER, connected=False, status="not_configured", scopes=[])
        ]
    st = await container.google.status(user_id)
    return [
        ConnectionOut(
            provider=PROVIDER, connected=st.connected, status=st.status, scopes=list(st.scopes)
        )
    ]


@router.post("/google/start", response_model=ConnectStartOut)
async def google_start(container: ContainerDep, user_id: UserDep) -> ConnectStartOut:
    return ConnectStartOut(auth_url=await _google(container).start(user_id))


@router.get("/google/callback", response_class=HTMLResponse, include_in_schema=False)
async def google_callback(
    container: ContainerDep, state: str = "", code: str = "", error: str = ""
) -> HTMLResponse:
    """Browser redirect target from Google. Not token-authenticated: it is protected by the
    single-use, expiring `state` issued to the authenticated user in /google/start."""
    if error or not state or not code:
        return _page(
            "Connessione annullata", "Google non ha concesso l'accesso. Puoi riprovare.", 400
        )
    try:
        await _google(container).complete(state, code)
    except GsoiError as exc:
        return _page("Connessione non riuscita", str(exc), 400)
    return _page(
        "Google collegato", "Fatto! Puoi chiudere questa scheda e tornare all'assistente.", 200
    )


@router.delete("/google", response_model=ConnectionOut)
async def google_disconnect(container: ContainerDep, user_id: UserDep) -> ConnectionOut:
    await _google(container).disconnect(user_id)
    return ConnectionOut(provider=PROVIDER, connected=False, status="not_connected", scopes=[])


def _page(title: str, message: str, status: int) -> HTMLResponse:
    body = (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        f"<title>{html.escape(title)}</title>"
        "<body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem'>"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></body>"
    )
    return HTMLResponse(body, status_code=status)
