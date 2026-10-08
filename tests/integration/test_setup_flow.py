"""Connecting a service must not require editing files: master key is automatic and the Google
application credentials are entered from /setup and stored encrypted."""

from __future__ import annotations

import re
import stat
import sys
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select

from conftest import TOKEN, make_settings
from gsoi_assistant.api.container import build_container
from gsoi_assistant.api.main import create_app
from gsoi_assistant.db import models
from gsoi_assistant.db.base import Base
from gsoi_assistant.security.keystore import KEY_FILE, resolve_master_key
from gsoi_assistant.security.secrets import EnvSecretStore
from support import make_tools

CID = "1234-abcd.apps.googleusercontent.com"
CSECRET = "GOCSPX-super-secret-value"


async def make_app(tmp_path: Path, outbox, cloud, local, *, env: dict | None = None):  # type: ignore[no-untyped-def]
    s = make_settings(tmp_path / "app.db", **({"google_client_id": CID} if env else {}))
    secrets = EnvSecretStore(env or {}, dotenv_path=None)
    c = build_container(
        s,
        secrets=secrets,
        providers={"reasoning": cloud, "private": local},
        extra_tools=make_tools(outbox),
    )
    async with c.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await c.hub.load()
    app = create_app(s, c)
    app.state.container = c
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    return c, client


# ---- master key --------------------------------------------------------------------------


def test_master_key_is_generated_once_and_reused(tmp_path: Path) -> None:
    secrets = EnvSecretStore({}, dotenv_path=None)
    first = resolve_master_key(secrets, "GSOI_MASTER_KEY", tmp_path / "d")
    assert resolve_master_key(secrets, "GSOI_MASTER_KEY", tmp_path / "d") == first
    assert len(first) == 44  # a valid Fernet key
    if sys.platform != "win32":
        assert stat.S_IMODE((tmp_path / "d" / KEY_FILE).stat().st_mode) == 0o600


def test_explicit_master_key_wins_over_the_file(tmp_path: Path) -> None:
    secrets = EnvSecretStore({"GSOI_MASTER_KEY": "explicit-key"}, dotenv_path=None)
    assert resolve_master_key(secrets, "GSOI_MASTER_KEY", tmp_path) == "explicit-key"
    assert not (tmp_path / KEY_FILE).exists()


# ---- configuration from the UI -----------------------------------------------------------


async def test_google_can_be_configured_without_touching_files(
    tmp_path, outbox, cloud, local
) -> None:  # type: ignore[no-untyped-def]
    c, client = await make_app(tmp_path, outbox, cloud, local)
    try:
        before = (await client.get("/v1/connections")).json()[0]
        assert before["configured"] is False and before["status"] == "not_configured"
        assert before["redirect_uri"].endswith("/v1/connections/google/callback")
        assert (await client.post("/v1/connections/google/start")).status_code == 409

        r = await client.put(
            "/v1/connections/google/config", json={"client_id": CID, "client_secret": CSECRET}
        )
        assert r.status_code == 200
        body = r.json()[0] if isinstance(r.json(), list) else r.json()
        assert (
            body["configured"] is True and body["config_source"] == "app" and not body["connected"]
        )
        assert CSECRET not in r.text

        # No restart: the very same running app can now start the consent flow.
        start = await client.post("/v1/connections/google/start")
        assert start.status_code == 200 and CID in start.json()["auth_url"]
    finally:
        await client.aclose()
        await c.aclose()


async def test_saved_secret_is_encrypted_at_rest_and_survives_restart(
    tmp_path, outbox, cloud, local
) -> None:  # type: ignore[no-untyped-def]
    c, client = await make_app(tmp_path, outbox, cloud, local)
    await client.put(
        "/v1/connections/google/config", json={"client_id": CID, "client_secret": CSECRET}
    )
    async with c.repo._sf() as s:
        row = (await s.execute(select(models.ConnectorConfig))).scalar_one()
    assert row.client_id == CID and CSECRET not in row.client_secret_enc
    await client.aclose()
    await c.aclose()

    # "Restart": a fresh container on the same database and data dir.
    c2, client2 = await make_app(tmp_path, outbox, cloud, local)
    try:
        st = (await client2.get("/v1/connections")).json()[0]
        assert st["configured"] is True and st["config_source"] == "app"
        assert c2.hub.auth is not None
    finally:
        await client2.aclose()
        await c2.aclose()


@pytest.mark.parametrize(
    "bad",
    [
        {"client_id": "not-a-google-id", "client_secret": CSECRET},
        {"client_id": CID, "client_secret": "short"},
    ],
)
async def test_bad_credentials_are_rejected_with_a_reason(
    tmp_path, outbox, cloud, local, bad
) -> None:  # type: ignore[no-untyped-def]
    c, client = await make_app(tmp_path, outbox, cloud, local)
    try:
        r = await client.put("/v1/connections/google/config", json=bad)
        assert r.status_code in (400, 422)
        assert (await client.get("/v1/connections")).json()[0]["configured"] is False
    finally:
        await client.aclose()
        await c.aclose()


async def test_changing_the_oauth_app_disconnects_accounts_of_the_old_one(
    tmp_path, outbox, cloud, local
) -> None:  # type: ignore[no-untyped-def]
    from support import connect_google

    c, client = await make_app(tmp_path, outbox, cloud, local)
    try:
        await client.put(
            "/v1/connections/google/config", json={"client_id": CID, "client_secret": CSECRET}
        )
        await connect_google(c)
        assert (await client.get("/v1/connections")).json()[0]["connected"] is True
        same = await client.put(
            "/v1/connections/google/config",
            json={"client_id": CID, "client_secret": "GOCSPX-rotated-secret"},
        )
        assert (
            same.status_code == 200
            and (await client.get("/v1/connections")).json()[0]["connected"] is True
        )
        other = "9999-zzzz.apps.googleusercontent.com"
        await client.put(
            "/v1/connections/google/config", json={"client_id": other, "client_secret": CSECRET}
        )
        assert (await client.get("/v1/connections")).json()[0]["connected"] is False
    finally:
        await client.aclose()
        await c.aclose()


async def test_remove_configuration(tmp_path, outbox, cloud, local) -> None:  # type: ignore[no-untyped-def]
    c, client = await make_app(tmp_path, outbox, cloud, local)
    try:
        await client.put(
            "/v1/connections/google/config", json={"client_id": CID, "client_secret": CSECRET}
        )
        r = await client.delete("/v1/connections/google/config")
        assert r.status_code == 200 and r.json()["configured"] is False
        assert c.hub.auth is None
    finally:
        await client.aclose()
        await c.aclose()


async def test_environment_configuration_takes_precedence_and_is_read_only(
    tmp_path, outbox, cloud, local
) -> None:  # type: ignore[no-untyped-def]
    c, client = await make_app(
        tmp_path, outbox, cloud, local, env={"GOOGLE_CLIENT_SECRET": CSECRET}
    )
    try:
        st = (await client.get("/v1/connections")).json()[0]
        assert st["configured"] is True and st["config_source"] == "env"
        r = await client.put(
            "/v1/connections/google/config", json={"client_id": CID, "client_secret": CSECRET}
        )
        assert r.status_code == 409
        assert (await client.delete("/v1/connections/google/config")).status_code == 409
    finally:
        await client.aclose()
        await c.aclose()


async def test_tools_say_google_is_not_set_up_instead_of_crashing(
    tmp_path, outbox, cloud, local
) -> None:  # type: ignore[no-untyped-def]
    from gsoi_assistant.llm.base import ToolCall
    from gsoi_assistant.tools.executor import ExecContext
    from support import new_run

    c, client = await make_app(tmp_path, outbox, cloud, local)
    try:
        ctx = ExecContext(user_id="owner", run_id=await new_run(c))
        r = await c.executor.execute(ToolCall(id="1", name="email.search", arguments={}), ctx)
        assert r.status == "error" and "/setup" in r.content
    finally:
        await client.aclose()
        await c.aclose()


async def test_configure_then_connect_then_use_in_one_running_app(
    tmp_path, outbox, cloud, local
) -> None:  # type: ignore[no-untyped-def]
    """The whole user journey with no file edits and no restart."""
    from urllib.parse import parse_qs, urlparse

    from gsoi_assistant.connectors.google.auth import DEFAULT_SCOPES, TOKEN_URL

    c, client = await make_app(tmp_path, outbox, cloud, local)
    try:
        await client.put(
            "/v1/connections/google/config", json={"client_id": CID, "client_secret": CSECRET}
        )
        url = (await client.post("/v1/connections/google/start")).json()["auth_url"]
        state = parse_qs(urlparse(url).query)["state"][0]
        with respx.mock(assert_all_called=False) as router:
            router.post(TOKEN_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "access_token": "a",
                        "refresh_token": "r",
                        "expires_in": 3600,
                        "scope": " ".join(DEFAULT_SCOPES),
                    },
                )
            )
            anon = httpx.AsyncClient(transport=client._transport, base_url="http://test")
            assert (
                await anon.get(
                    "/v1/connections/google/callback", params={"state": state, "code": "x"}
                )
            ).status_code == 200
            router.get("https://gmail.googleapis.com/gmail/v1/users/me/messages").mock(
                return_value=httpx.Response(200, json={})
            )
            cloud._queue = [
                __import__("gsoi_assistant.llm.testing", fromlist=["use"]).use("email.search"),
                "Nessuna email.",
            ]
            reply = await client.post("/v1/chat", json={"message": "ho email?"})
        assert reply.json()["content"] == "Nessuna email."
        actions = [e.action for e in await c.audit._store.all()]
        assert {"connector.configured", "connector.connected"} <= set(actions)
    finally:
        await client.aclose()
        await c.aclose()


# ---- the /setup page ---------------------------------------------------------------------


async def test_setup_page_is_served_with_a_strict_csp(client: httpx.AsyncClient) -> None:
    anon = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    r = await anon.get("/setup")  # public: it holds no secrets and asks for the token itself
    assert r.status_code == 200 and "Collega i tuoi servizi" in r.text
    csp = r.headers["content-security-policy"]
    nonce = re.search(r"script-src 'nonce-([^']+)'", csp).group(1)  # type: ignore[union-attr]
    assert f'<script nonce="{nonce}">' in r.text and "unsafe-inline" not in csp
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["cache-control"] == "no-store"
    other = (await anon.get("/setup")).headers["content-security-policy"]
    assert other != csp  # fresh nonce per request


async def test_setup_page_has_no_secrets_and_no_dom_injection_sinks(
    client: httpx.AsyncClient,
) -> None:
    html = (await client.get("/setup")).text
    for forbidden in (
        TOKEN,
        "innerHTML",
        "outerHTML",
        "document.write",
        "eval(",
        "insertAdjacentHTML",
    ):
        assert forbidden not in html


async def test_setup_page_uses_only_the_connection_api(client: httpx.AsyncClient) -> None:
    html = (await client.get("/setup")).text
    paths = set(re.findall(r'"(/v1/[a-z/_]+)"', html))
    assert paths == {
        "/v1/connections",
        "/v1/connections/google/config",
        "/v1/connections/google/start",
        "/v1/connections/google",
    }
    spec = (await client.get("/openapi.json")).json()["paths"]
    for p in paths:
        assert p in spec
