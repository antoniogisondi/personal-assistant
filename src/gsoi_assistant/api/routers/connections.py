from __future__ import annotations

import html

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from gsoi_assistant.api.deps import ContainerDep, UserDep
from gsoi_assistant.api.schemas import (
    ConnectionOut,
    ConnectStartOut,
    GoogleConfigBody,
)
from gsoi_assistant.connectors.google.auth import PROVIDER
from gsoi_assistant.core.errors import GsoiError

router = APIRouter(prefix="/v1/connections", tags=["connections"])


async def _status(container: ContainerDep, user_id: str) -> ConnectionOut:
    hub = container.hub
    if hub.auth is None:
        return ConnectionOut(
            provider=PROVIDER,
            configured=False,
            connected=False,
            status="not_configured",
            scopes=[],
            redirect_uri=hub.redirect_uri,
        )
    st = await hub.auth.status(user_id)
    return ConnectionOut(
        provider=PROVIDER,
        configured=True,
        config_source=hub.source,
        connected=st.connected,
        status=st.status,
        scopes=list(st.scopes),
        redirect_uri=hub.redirect_uri,
    )


@router.get("", response_model=list[ConnectionOut])
async def list_connections(container: ContainerDep, user_id: UserDep) -> list[ConnectionOut]:
    return [await _status(container, user_id)]


@router.put("/google/config", response_model=ConnectionOut)
async def google_configure(
    body: GoogleConfigBody, container: ContainerDep, user_id: UserDep
) -> ConnectionOut:
    """Save the Google OAuth application credentials (encrypted). No restart needed."""
    await container.hub.configure(body.client_id, body.client_secret)
    await container.audit.record(
        user_id=user_id, actor="user", action="connector.configured", subject=PROVIDER
    )
    return await _status(container, user_id)


@router.delete("/google/config", response_model=ConnectionOut)
async def google_unconfigure(container: ContainerDep, user_id: UserDep) -> ConnectionOut:
    await container.hub.clear()
    await container.audit.record(
        user_id=user_id, actor="user", action="connector.unconfigured", subject=PROVIDER
    )
    return await _status(container, user_id)


@router.post("/google/start", response_model=ConnectStartOut)
async def google_start(container: ContainerDep, user_id: UserDep) -> ConnectStartOut:
    return ConnectStartOut(auth_url=await container.hub.require().start(user_id))


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
        user_id = await container.hub.require().complete(state, code)
    except GsoiError as exc:
        return _page("Connessione non riuscita", str(exc), 400)
    await container.audit.record(
        user_id=user_id, actor="user", action="connector.connected", subject=PROVIDER
    )
    return _page(
        "Google collegato", "Fatto! Puoi chiudere questa scheda e tornare all'assistente.", 200
    )


@router.delete("/google", response_model=ConnectionOut)
async def google_disconnect(container: ContainerDep, user_id: UserDep) -> ConnectionOut:
    if container.hub.auth is not None:
        await container.hub.auth.disconnect(user_id)
        await container.audit.record(
            user_id=user_id, actor="user", action="connector.disconnected", subject=PROVIDER
        )
    return await _status(container, user_id)


def _page(title: str, message: str, status: int) -> HTMLResponse:
    body = (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        f"<title>{html.escape(title)}</title>"
        "<body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem'>"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></body>"
    )
    return HTMLResponse(body, status_code=status)
