from __future__ import annotations

import asyncio
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from sqlalchemy import select

from gsoi_assistant.api.container import Container, build_container
from gsoi_assistant.connectors.google.auth import (
    DEFAULT_SCOPES,
    REVOKE_URL,
    TOKEN_URL,
)
from gsoi_assistant.core.errors import (
    BadRequestError,
    ConnectorNotConnectedError,
    ToolError,
)
from gsoi_assistant.db import models
from gsoi_assistant.db.stores import utcnow
from gsoi_assistant.security.secrets import EnvSecretStore, SecretNotFoundError
from support import connect_google

TOKEN_OK = {
    "access_token": "new-access",
    "refresh_token": "new-refresh-SECRET",
    "expires_in": 3599,
    "scope": " ".join(DEFAULT_SCOPES),
    "token_type": "Bearer",
}


async def begin(c: Container) -> tuple[str, dict[str, list[str]]]:
    url = await c.google.start("owner")  # type: ignore[union-attr]
    q = parse_qs(urlparse(url).query)
    return q["state"][0], q


async def test_start_builds_a_pkce_consent_url(gcontainer: Container) -> None:
    state, q = await begin(gcontainer)
    assert q["client_id"] == ["client-id.apps.googleusercontent.com"]
    assert q["response_type"] == ["code"] and q["access_type"] == ["offline"]
    assert q["code_challenge_method"] == ["S256"] and len(q["code_challenge"][0]) == 43
    assert set(q["scope"][0].split()) == set(DEFAULT_SCOPES)
    assert len(state) >= 32
    assert "client-secret-value" not in str(q)  # the secret never goes in the browser URL


async def test_scopes_are_least_privilege() -> None:
    # No full-mailbox / delete / admin scopes: readonly + compose (drafts, send) + calendar.
    assert not any(
        s.endswith(("/mail.google.com/", "/gmail.modify", "/calendar")) for s in DEFAULT_SCOPES
    )


@respx.mock
async def test_complete_stores_encrypted_tokens(gcontainer: Container) -> None:
    state, _ = await begin(gcontainer)
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_OK))
    assert await gcontainer.google.complete(state, "auth-code") == "owner"  # type: ignore[union-attr]

    sent = parse_qs(route.calls[0].request.content.decode())
    assert sent["grant_type"] == ["authorization_code"] and sent["code"] == ["auth-code"]
    assert sent["client_secret"] == ["client-secret-value"] and len(sent["code_verifier"][0]) >= 43

    async with gcontainer.repo._sf() as s:
        row = (await s.execute(select(models.OAuthCredential))).scalar_one()
    assert "new-refresh-SECRET" not in row.token_enc and "new-access" not in row.token_enc
    assert row.status == "active" and set(row.scopes) == set(DEFAULT_SCOPES)
    assert await gcontainer.google.access_token("owner") == "new-access"  # type: ignore[union-attr]


@respx.mock
async def test_state_is_single_use_and_unknown_state_rejected(gcontainer: Container) -> None:
    state, _ = await begin(gcontainer)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_OK))
    await gcontainer.google.complete(state, "c")  # type: ignore[union-attr]
    with pytest.raises(BadRequestError):
        await gcontainer.google.complete(state, "c")  # type: ignore[union-attr]  # replay
    with pytest.raises(BadRequestError):
        await gcontainer.google.complete("forged-state", "c")  # type: ignore[union-attr]


@respx.mock
async def test_expired_state_is_rejected(gcontainer: Container) -> None:
    from sqlalchemy import update

    state, _ = await begin(gcontainer)
    async with gcontainer.repo._sf() as s, s.begin():
        await s.execute(
            update(models.OAuthState).values(expires_at=utcnow() - timedelta(seconds=1))
        )
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_OK))
    with pytest.raises(BadRequestError):
        await gcontainer.google.complete(state, "c")  # type: ignore[union-attr]


@respx.mock
async def test_missing_refresh_token_is_explained(gcontainer: Container) -> None:
    state, _ = await begin(gcontainer)
    body = {k: v for k, v in TOKEN_OK.items() if k != "refresh_token"}
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(BadRequestError, match="refresh token"):
        await gcontainer.google.complete(state, "c")  # type: ignore[union-attr]


@respx.mock
async def test_access_token_is_cached_then_refreshed_when_expired(gcontainer: Container) -> None:
    await connect_google(gcontainer, expires_in=3600)
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "refreshed", "expires_in": 3600})
    )
    assert await gcontainer.google.access_token("owner") == "access-token-abc"  # type: ignore[union-attr]
    assert route.call_count == 0

    await connect_google(gcontainer, expires_in=10)  # inside the 60 s safety margin
    assert await gcontainer.google.access_token("owner") == "refreshed"  # type: ignore[union-attr]
    form = parse_qs(route.calls[0].request.content.decode())
    assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == [
        "refresh-token-123"
    ]
    assert await gcontainer.google.access_token("owner") == "refreshed"  # type: ignore[union-attr]
    assert route.call_count == 1  # new token cached and persisted


@respx.mock
async def test_concurrent_requests_trigger_a_single_refresh(gcontainer: Container) -> None:
    await connect_google(gcontainer, expires_in=-5)
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "once", "expires_in": 3600})
    )
    tokens = await asyncio.gather(*(gcontainer.google.access_token("owner") for _ in range(8)))  # type: ignore[union-attr]
    assert set(tokens) == {"once"} and route.call_count == 1


@respx.mock
async def test_revoked_grant_marks_needs_reauth_and_fails_fast(gcontainer: Container) -> None:
    await connect_google(gcontainer, expires_in=-5)
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    with pytest.raises(ConnectorNotConnectedError, match="connect it again"):
        await gcontainer.google.access_token("owner")  # type: ignore[union-attr]
    st = await gcontainer.google.status("owner")  # type: ignore[union-attr]
    assert st.status == "needs_reauth" and not st.connected
    with pytest.raises(ConnectorNotConnectedError):
        await gcontainer.google.access_token("owner")  # type: ignore[union-attr]
    assert route.call_count == 1  # no hammering Google with a dead token


@respx.mock
async def test_google_outage_during_refresh_is_a_tool_error_not_a_crash(
    gcontainer: Container,
) -> None:
    await connect_google(gcontainer, expires_in=-5)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(ToolError, match="rejected"):
        await gcontainer.google.access_token("owner")  # type: ignore[union-attr]
    assert (await gcontainer.google.status("owner")).status == "active"  # type: ignore[union-attr]


async def test_not_connected_user(gcontainer: Container) -> None:
    with pytest.raises(ConnectorNotConnectedError, match="not connected"):
        await gcontainer.google.access_token("owner")  # type: ignore[union-attr]
    assert (await gcontainer.google.status("owner")).status == "not_connected"  # type: ignore[union-attr]


@respx.mock
async def test_disconnect_revokes_and_deletes(gcontainer: Container) -> None:
    await connect_google(gcontainer)
    revoke = respx.post(REVOKE_URL).mock(return_value=httpx.Response(200))
    assert await gcontainer.google.disconnect("owner") is True  # type: ignore[union-attr]
    assert parse_qs(revoke.calls[0].request.content.decode())["token"] == ["refresh-token-123"]
    assert (await gcontainer.google.status("owner")).status == "not_connected"  # type: ignore[union-attr]
    assert await gcontainer.google.disconnect("owner") is False  # type: ignore[union-attr]


@respx.mock
async def test_credentials_are_per_user(gcontainer: Container) -> None:
    await connect_google(gcontainer, user_id="alice")
    with pytest.raises(ConnectorNotConnectedError):
        await gcontainer.google.access_token("bob")  # type: ignore[union-attr]
    assert await gcontainer.google.access_token("alice") == "access-token-abc"  # type: ignore[union-attr]


# ---- HTTP endpoints ----------------------------------------------------------------------


@respx.mock
async def test_full_browser_flow_over_http(gclient: httpx.AsyncClient) -> None:
    assert (await gclient.get("/v1/connections")).json()[0]["status"] == "not_connected"
    auth_url = (await gclient.post("/v1/connections/google/start")).json()["auth_url"]
    state = parse_qs(urlparse(auth_url).query)["state"][0]

    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_OK))
    anon = httpx.AsyncClient(transport=gclient._transport, base_url="http://test")
    page = await anon.get("/v1/connections/google/callback", params={"state": state, "code": "abc"})
    assert page.status_code == 200 and "Google collegato" in page.text

    conn = (await gclient.get("/v1/connections")).json()[0]
    assert conn["connected"] is True and set(conn["scopes"]) == set(DEFAULT_SCOPES)

    respx.post(REVOKE_URL).mock(return_value=httpx.Response(200))
    assert (await gclient.delete("/v1/connections/google")).json()["connected"] is False


async def test_callback_rejects_bad_requests_and_escapes_output(gclient: httpx.AsyncClient) -> None:
    anon = httpx.AsyncClient(transport=gclient._transport, base_url="http://test")
    denied = await anon.get("/v1/connections/google/callback", params={"error": "access_denied"})
    assert denied.status_code == 400
    forged = await anon.get(
        "/v1/connections/google/callback",
        params={"state": "<script>alert(1)</script>", "code": "x"},
    )
    assert forged.status_code == 400 and "<script>" not in forged.text


async def test_connection_endpoints_require_the_api_token(gclient: httpx.AsyncClient) -> None:
    anon = httpx.AsyncClient(transport=gclient._transport, base_url="http://test")
    assert (await anon.get("/v1/connections")).status_code == 401
    assert (await anon.post("/v1/connections/google/start")).status_code == 401
    assert (await anon.delete("/v1/connections/google")).status_code == 401


async def test_google_disabled_by_default(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/connections")).json()[0]["status"] == "not_configured"
    started = await client.post("/v1/connections/google/start")
    assert started.status_code == 409 and "/setup" in started.json()["error"]["message"]


async def test_env_client_id_without_its_secret_fails_at_startup_with_a_clear_name(  # type: ignore[no-untyped-def]
    tmp_path, cloud, local
) -> None:
    from conftest import make_settings

    s = make_settings(tmp_path / "x.db", google_client_id="cid")
    secrets = EnvSecretStore({}, dotenv_path=None)
    with pytest.raises(SecretNotFoundError, match="GOOGLE_CLIENT_SECRET"):
        build_container(s, secrets=secrets, providers={"reasoning": cloud, "private": local})
