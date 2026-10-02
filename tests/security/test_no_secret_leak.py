from __future__ import annotations

import logging

import httpx
import structlog

from gsoi_assistant.core.errors import ProviderAuthError
from gsoi_assistant.llm.testing import ScriptedProvider
from gsoi_assistant.observability.logging import configure_logging

SECRET = "sk-canary-0123456789abcdef"


async def test_secrets_never_reach_logs(
    client: httpx.AsyncClient, cloud: ScriptedProvider, capfd: object
) -> None:
    configure_logging(level="DEBUG", json_logs=True)
    try:
        structlog.get_logger("t").info("probe", api_key=SECRET, note=f"token {SECRET}")
        cloud._queue = [ProviderAuthError(f"bad key {SECRET}")]
        r = await client.post("/v1/chat", json={"message": "hello"})
        assert SECRET not in r.text
        out, err = capfd.readouterr()  # type: ignore[attr-defined]
    finally:
        logging.getLogger().handlers.clear()  # don't keep a handler bound to captured stdout
        structlog.reset_defaults()
    assert "probe" in out  # logging is actually captured
    assert SECRET not in out + err


async def test_api_requires_token_for_every_v1_route(client: httpx.AsyncClient) -> None:
    anon = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    for method, path in [
        ("POST", "/v1/chat"),
        ("POST", "/v1/chat/stream"),
        ("GET", "/v1/conversations/x/messages"),
    ]:
        r = await anon.request(method, path, json={"message": "x"})
        assert r.status_code in (401, 422)
        assert r.status_code != 200
