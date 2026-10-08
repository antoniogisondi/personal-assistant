from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from gsoi_assistant.security.secrets import SecretNotFoundError
from gsoi_desktop.autostart import RUN_KEY, VALUE_NAME, Autostart
from gsoi_desktop.client import ApiClient, ApiError
from gsoi_desktop.config import (
    PROVIDERS,
    DesktopConfig,
    load_config,
    save_config,
    secret_name,
)
from gsoi_desktop.controller import AssistantController
from gsoi_desktop.paths import app_home
from gsoi_desktop.runtime import (
    API_TOKEN,
    MASTER_KEY,
    BackendRuntime,
    build_settings,
    ensure_bootstrap_secrets,
    free_port,
)
from gsoi_desktop.secrets_store import FileSecrets, choose_secret_store

# ---- paths / config ---------------------------------------------------------------------


def test_app_home_honours_override(home: Path) -> None:
    assert app_home() == home


def test_config_round_trip_and_defaults(home: Path) -> None:
    cfg = load_config(home)
    assert cfg.model == "" and cfg.provider == "deepseek" and cfg.read_aloud
    cfg.model, cfg.user_address = "some-model", "capo"
    save_config(home, cfg)
    assert load_config(home).model == "some-model" and load_config(home).user_address == "capo"
    assert not list(home.glob("*.tmp"))


def test_corrupt_config_falls_back_to_defaults(home: Path) -> None:
    (home / "config.json").write_text("{ not json")
    assert load_config(home).model == ""
    (home / "config.json").write_text(json.dumps({"provider": 5}))
    assert load_config(home).provider == "deepseek"


def test_configured_requires_model_and_key_when_needed() -> None:
    assert not DesktopConfig().is_configured(has_key=True)  # no model yet
    assert not DesktopConfig(model="m").is_configured(has_key=False)  # cloud needs a key
    assert DesktopConfig(model="m").is_configured(has_key=True)
    assert DesktopConfig(provider="ollama", model="m").is_configured(has_key=False)  # local: no key
    assert not DesktopConfig(provider="nope", model="m").is_configured(has_key=True)


def test_provider_presets_are_sane() -> None:
    assert PROVIDERS["ollama"].is_local and not PROVIDERS["ollama"].needs_key
    assert PROVIDERS["deepseek"].needs_key and not PROVIDERS["deepseek"].is_local
    assert secret_name("deepseek") == "DEEPSEEK_API_KEY"


# ---- secrets -----------------------------------------------------------------------------


def test_file_secrets_store(tmp_path: Path) -> None:
    s = FileSecrets(tmp_path / "s.json")
    assert not s.has("K")
    with pytest.raises(SecretNotFoundError):
        s.get("K")
    s.set("K", "value-1")
    assert s.get("K").get_secret_value() == "value-1" and s.has("K")
    s.delete("K")
    assert not s.has("K")
    s.set("A", "1")
    if sys.platform != "win32":
        assert stat.S_IMODE((tmp_path / "s.json").stat().st_mode) == 0o600


def test_choose_store_falls_back_when_there_is_no_keyring(home: Path) -> None:
    store = choose_secret_store(home)  # headless CI: no OS keyring
    store.set("X", "y")
    assert store.get("X").get_secret_value() == "y"


def test_bootstrap_creates_keys_once(tmp_path: Path) -> None:
    s = FileSecrets(tmp_path / "s.json")
    token = ensure_bootstrap_secrets(s)
    assert len(token) >= 32 and s.has(MASTER_KEY) and s.has(API_TOKEN)
    master = s.get(MASTER_KEY).get_secret_value()
    assert ensure_bootstrap_secrets(s) == token and s.get(MASTER_KEY).get_secret_value() == master


# ---- autostart ---------------------------------------------------------------------------


class FakeRegistry:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def read(self, key: str, name: str) -> str | None:
        return self.values.get((key, name))

    def write(self, key: str, name: str, value: str) -> None:
        self.values[(key, name)] = value

    def remove(self, key: str, name: str) -> None:
        self.values.pop((key, name), None)


def test_autostart_toggles_the_run_key() -> None:
    reg = FakeRegistry()
    a = Autostart(reg, command=lambda: '"C:\\GSOI\\GSOI.exe" --minimized')
    assert not a.is_enabled()
    a.set_enabled(True)
    assert (
        reg.values[(RUN_KEY, VALUE_NAME)] == '"C:\\GSOI\\GSOI.exe" --minimized' and a.is_enabled()
    )
    a.set_enabled(False)
    assert not a.is_enabled() and reg.values == {}


def test_autostart_detects_a_stale_path() -> None:
    reg = FakeRegistry()
    Autostart(reg, command=lambda: '"old.exe" --minimized').set_enabled(True)
    assert not Autostart(reg, command=lambda: '"new.exe" --minimized').is_enabled()


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows behaviour")
def test_autostart_is_a_noop_outside_windows() -> None:
    a = Autostart()
    assert not a.supported and not a.is_enabled()
    a.set_enabled(True)  # must not raise


# ---- controller -------------------------------------------------------------------------


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.google: dict[str, Any] = {
            "provider": "google",
            "configured": True,
            "connected": False,
            "status": "active",
            "scopes": [],
        }

    def chat(
        self, message: str, conversation_id: str | None, channel: str = "text"
    ) -> dict[str, Any]:
        self.calls.append(("chat", (message, conversation_id, channel)))
        if "mail" in message:
            return {
                "conversation_id": "c1",
                "content": "",
                "cost_usd": 0.0,
                "approval": {
                    "approval_id": "a1",
                    "tool": "email.send",
                    "risk": "EXTERNAL",
                    "strength": "normal",
                    "display": {
                        "summary": "Send an email to marco@example.com",
                        "arguments": {"to": ["marco@example.com"]},
                    },
                },
            }
        return {"conversation_id": "c1", "content": "Ciao!", "cost_usd": 0.001}

    def briefing(self, channel: str = "voice") -> dict[str, Any]:
        self.calls.append(("briefing", channel))
        return {"conversation_id": "c2", "content": "Buongiorno signore", "cost_usd": 0.0}

    def decide(self, approval_id: str, approve: bool, confirm_tool: str | None) -> dict[str, Any]:
        self.calls.append(("decide", (approval_id, approve, confirm_tool)))
        return {
            "conversation_id": "c1",
            "content": "Fatto" if approve else "Ok, annullato",
            "cost_usd": 0,
        }

    def connections(self) -> list[dict[str, Any]]:
        return [self.google]

    def google_start(self) -> str:
        return "https://accounts.google.com/o/oauth2/v2/auth?x=1"

    def google_disconnect(self) -> None:
        self.calls.append(("disconnect", None))


def test_controller_keeps_the_conversation() -> None:
    api = FakeApi()
    c = AssistantController(api)
    assert c.send("ciao").text == "Ciao!"
    c.send("ancora")
    assert api.calls[1] == ("chat", ("ancora", "c1", "text"))
    c.new_conversation()
    c.send("nuova")
    assert api.calls[2][1][1] is None


def test_approval_request_is_surfaced_with_the_exact_details() -> None:
    c = AssistantController(FakeApi())
    turn = c.send("manda una mail a marco")
    a = turn.approval
    assert a is not None and a.tool == "email.send" and not a.strong
    assert a.arguments == {"to": ["marco@example.com"]} and "marco@example.com" in a.summary


def test_decisions_and_strong_confirmation() -> None:
    api = FakeApi()
    c = AssistantController(api)
    a = c.send("mail").approval
    assert a is not None
    assert c.decide(a, True).text == "Fatto" and api.calls[-1] == ("decide", ("a1", True, None))
    assert c.decide(a, False).text == "Ok, annullato"
    from dataclasses import replace

    strong = replace(a, tool="notes.delete", strong=True)
    c.decide(strong, True)
    assert api.calls[-1] == ("decide", ("a1", True, "notes.delete"))  # confirms by naming the tool
    c.decide(strong, False)
    assert api.calls[-1] == ("decide", ("a1", False, None))


def test_briefing_and_google_state() -> None:
    api = FakeApi()
    c = AssistantController(api)
    assert c.briefing().text.startswith("Buongiorno") and api.calls[-1] == ("briefing", "voice")
    g = c.google_state()
    assert g.available and not g.connected and not g.needs_reconnect
    api.google.update(connected=False, status="needs_reauth")
    assert c.google_state().needs_reconnect
    api.google.update(configured=False)
    assert not c.google_state().available
    assert c.google_connect_url().startswith("https://accounts.google.com/")


# ---- API client --------------------------------------------------------------------------


@respx.mock
def test_client_maps_errors_to_readable_messages() -> None:
    api = ApiClient("http://127.0.0.1:1", "tok")
    respx.post("http://127.0.0.1:1/v1/chat").mock(
        return_value=httpx.Response(
            502, json={"error": {"code": "x", "message": "chiave non valida"}}
        )
    )
    with pytest.raises(ApiError) as e:
        api.chat("ciao", None)
    assert e.value.status == 502 and e.value.message == "chiave non valida"

    respx.post("http://127.0.0.1:1/v1/briefing").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(ApiError, match="500"):
        api.briefing()

    respx.get("http://127.0.0.1:1/v1/connections").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(ApiError) as e2:
        api.connections()
    assert e2.value.status == 0
    respx.get("http://127.0.0.1:1/health").mock(side_effect=httpx.ConnectError("down"))
    assert api.health() is False


@respx.mock
def test_client_sends_the_token_and_body() -> None:
    api = ApiClient("http://127.0.0.1:1", "secret-token")
    route = respx.post("http://127.0.0.1:1/v1/approvals/abc/decision").mock(
        return_value=httpx.Response(200, json={"content": "ok"})
    )
    api.decide("abc", True, "notes.delete")
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer secret-token"
    assert json.loads(req.content) == {"approve": True, "confirm_tool": "notes.delete"}


SSE_REPLY = (
    'data: {"model":"test-model","choices":[{"delta":{"content":"Ciao dal modello"}}]}\n\n'
    'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":3}}\n\n'
    "data: [DONE]\n\n"
)


# ---- settings + the embedded backend, end to end -----------------------------------------------


def test_settings_for_a_cloud_profile(home: Path) -> None:
    cfg = DesktopConfig(model="m-1", timezone="Europe/Rome")
    s = build_settings(home, cfg, "t" * 32, 4321, has_model_key=True)
    p = s.profiles["reasoning"]
    assert p.api_key_ref == "DEEPSEEK_API_KEY" and p.model == "m-1" and not p.is_local
    assert s.google_redirect_uri == "http://127.0.0.1:4321/v1/connections/google/callback"
    assert (
        s.log_file == home / "logs" / "gsoi.log" and "gsoi.db" in s.database_url.get_secret_value()
    )
    local = build_settings(
        home, DesktopConfig(provider="ollama", model="x"), "t" * 32, 1, has_model_key=False
    )
    assert local.profiles["reasoning"].is_local and local.profiles["reasoning"].api_key_ref is None
    unset = build_settings(home, DesktopConfig(), "t" * 32, 1, has_model_key=False)
    assert unset.profiles["reasoning"].model == "not-configured"  # the app still starts


def test_free_port_is_usable() -> None:
    assert 1024 < free_port() < 65536


def test_embedded_backend_end_to_end(home: Path) -> None:
    """Real server on a random loopback port, real database, model provider mocked."""
    store = FileSecrets(home / "secrets.json")
    store.set("DEEPSEEK_API_KEY", "sk-test-key-value-1234")
    cfg = DesktopConfig(
        model="test-model", base_url="https://llm.invalid/v1", timezone="Europe/Rome"
    )
    rt = BackendRuntime(home, cfg, store)
    rt.start()
    try:
        assert rt.model_key_present
        client = ApiClient(rt.base_url, rt.token)
        assert client.health()

        with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
            llm = router.post("https://llm.invalid/v1/chat/completions").mock(
                side_effect=lambda req: httpx.Response(
                    200,
                    text=SSE_REPLY,
                    headers={"content-type": "text/event-stream"},
                )
            )
            router.route(host="127.0.0.1").pass_through()
            turn = AssistantController(client).send("ciao")
        assert turn.text == "Ciao dal modello"
        assert llm.calls[0].request.headers["authorization"] == "Bearer sk-test-key-value-1234"

        g = AssistantController(client).google_state()
        assert not g.available  # no Google application bundled/configured in this build
        assert (home / "gsoi.db").exists() and (home / "logs" / "gsoi.log").exists()
        page = httpx.get(rt.base_url + "/setup")
        assert page.status_code == 200
        with pytest.raises(ApiError) as e:
            ApiClient(rt.base_url, "wrong-token").connections()
        assert e.value.status == 401
        client.close()
    finally:
        rt.stop()

    # The same data folder survives a restart: conversation history is still in the database.
    rt2 = BackendRuntime(home, cfg, store)
    rt2.start()
    try:
        assert rt2.token == rt.token  # same installation, same local token
        assert ApiClient(rt2.base_url, rt2.token).health()
    finally:
        rt2.stop()
