"""Runs the assistant backend inside the desktop app (loopback only, random port)."""

from __future__ import annotations

import secrets
import socket
import threading
import time
from pathlib import Path

import uvicorn
from cryptography.fernet import Fernet
from pydantic import SecretStr

from gsoi_assistant.api.container import build_container
from gsoi_assistant.api.main import create_app
from gsoi_assistant.config.settings import ModelProfile, Settings
from gsoi_assistant.connectors.google.hub import load_bundled_app
from gsoi_assistant.connectors.pc.backend import PcBackend, default_backend
from gsoi_assistant.connectors.pc.tools import make_pc_tools
from gsoi_assistant.db.migrate import upgrade_to_head
from gsoi_desktop.config import PROVIDERS, DesktopConfig, secret_name
from gsoi_desktop.secrets_store import WritableSecrets

MASTER_KEY = "GSOI_MASTER_KEY"
API_TOKEN = "GSOI_API_TOKEN"  # noqa: S105  # nosec B105


class _AutoPc:
    """Sentinel: use the real computer-control backend of this platform (None elsewhere)."""


AUTO_PC = _AutoPc()


class _SecretsView:
    """Adapts the writable store to the read-only SecretStore protocol the backend expects."""

    def __init__(self, store: WritableSecrets) -> None:
        self._store = store

    def get(self, name: str) -> SecretStr:
        return self._store.get(name)


def ensure_bootstrap_secrets(store: WritableSecrets) -> str:
    """Create the encryption key and the local API token on first run. Returns the API token."""
    if not store.has(MASTER_KEY):
        store.set(MASTER_KEY, Fernet.generate_key().decode())
    if not store.has(API_TOKEN):
        store.set(API_TOKEN, secrets.token_urlsafe(32))
    return store.get(API_TOKEN).get_secret_value()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def build_settings(
    home: Path, config: DesktopConfig, token: str, port: int, *, has_model_key: bool
) -> Settings:
    provider = PROVIDERS.get(config.provider, PROVIDERS["deepseek"])
    profile = ModelProfile(
        provider=provider.key,
        model=config.model.strip() or "not-configured",
        base_url=config.base_url.strip() or provider.base_url,
        api_key_ref=secret_name(provider.key) if provider.needs_key and has_model_key else None,
        is_local=provider.is_local,
        tool_calling=config.tool_calling,
    )
    return Settings(
        _env_file=None,
        env="prod",
        database_url=SecretStr(f"sqlite+aiosqlite:///{(home / 'gsoi.db').as_posix()}"),
        api_token=SecretStr(token),
        profiles={"reasoning": profile},
        default_profile="reasoning",
        data_dir=home / "data",
        log_file=home / "logs" / "gsoi.log",
        log_level="INFO",
        timezone=config.timezone,
        user_address=config.user_address,
        google_redirect_uri=f"http://127.0.0.1:{port}/v1/connections/google/callback",
    )


class BackendRuntime:
    def __init__(
        self,
        home: Path,
        config: DesktopConfig,
        store: WritableSecrets,
        pc: PcBackend | _AutoPc | None = AUTO_PC,
    ) -> None:
        self.home = home
        self._pc: PcBackend | None = default_backend() if isinstance(pc, _AutoPc) else pc
        self.config = config
        self._store = store
        self.token = ensure_bootstrap_secrets(store)
        self.port = 0
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def model_key_present(self) -> bool:
        provider = PROVIDERS.get(self.config.provider)
        return bool(provider and self._store.has(secret_name(provider.key)))

    def start(self, timeout: float = 30.0) -> None:
        if self._thread is not None:
            return
        self.port = free_port()
        settings = build_settings(
            self.home, self.config, self.token, self.port, has_model_key=self.model_key_present
        )
        upgrade_to_head(settings.database_url.get_secret_value())
        # The Google application: a file dropped in the data folder wins over the one in the build.
        google_app = load_bundled_app(self.home / "google_app.json") or load_bundled_app()
        container = build_container(
            settings,
            secrets=_SecretsView(self._store),
            bundled_google_app=google_app,
            extra_tools=make_pc_tools(self._pc) if self._pc is not None else None,
        )
        app = create_app(settings, container)
        # log_config=None: uvicorn's default log setup assumes a console (breaks in a windowed exe)
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_config=None, lifespan="on")
        )
        self._thread = threading.Thread(target=self._server.run, name="gsoi-backend", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("the assistant's internal service did not start")
            time.sleep(0.05)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._server, self._thread = None, None

    def app_names(self) -> list[str]:
        """Installed programs, to help the voice recogniser with their names."""
        if self._pc is None:
            return []
        try:
            return [a.name for a in self._pc.apps()]
        except Exception:
            return []

    def restart(self, config: DesktopConfig) -> None:
        self.stop()
        self.config = config
        self.start()


__all__ = ["BackendRuntime", "build_settings", "ensure_bootstrap_secrets"]
