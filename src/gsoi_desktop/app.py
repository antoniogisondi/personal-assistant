"""Entry point of the GSOI desktop app."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import traceback
from pathlib import Path

import httpx

from gsoi_desktop import __version__
from gsoi_desktop.config import DesktopConfig, load_config, save_config, secret_name
from gsoi_desktop.paths import app_home
from gsoi_desktop.runtime import BackendRuntime
from gsoi_desktop.secrets_store import FileSecrets, choose_secret_store


def selftest(home: Path | None = None) -> int:
    """Headless check used by the build: the bundle can start its backend and serve requests."""
    return _record(home, _selftest(home))


def _voice_selftest() -> str | None:
    """The voice libraries and the bundled wake-word model load and run (None when all is well)."""
    try:
        import ctranslate2  # noqa: F401
        import faster_whisper  # noqa: F401
        import numpy as np
        import sounddevice  # noqa: F401

        from gsoi_desktop.voice.wake import OpenWakeWordDetector, wake_models_present

        if not wake_models_present():
            return "wake-word models are missing from the bundle"
        score = OpenWakeWordDetector().predict(np.zeros(1280, dtype=np.int16))
        return None if 0.0 <= score <= 1.0 else f"unexpected wake score {score}"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def _record(home: Path | None, code: int) -> int:
    env_home = os.environ.get("GSOI_DESKTOP_HOME")
    target = home or (Path(env_home) if env_home else None)
    if target is not None:  # a windowed exe has no console: leave the verdict in a file
        (target / "selftest.txt").write_text("OK\n" if code == 0 else "FAILED\n", encoding="utf-8")
    return code


def _selftest(home: Path | None) -> int:
    env_home = os.environ.get("GSOI_DESKTOP_HOME")
    work = home or (Path(env_home) if env_home else Path(tempfile.mkdtemp(prefix="gsoi-selftest-")))
    work.mkdir(parents=True, exist_ok=True)
    runtime = BackendRuntime(work, DesktopConfig(), FileSecrets(work / "secrets.json"))
    try:
        runtime.start()
        auth = {"Authorization": f"Bearer {runtime.token}"}
        checks = {
            "health": httpx.get(f"{runtime.base_url}/health", timeout=10),
            "ready": httpx.get(f"{runtime.base_url}/ready", timeout=10),
            "setup page": httpx.get(f"{runtime.base_url}/setup", timeout=10),
            "authenticated api": httpx.get(
                f"{runtime.base_url}/v1/connections", headers=auth, timeout=10
            ),
        }
        failed = [name for name, r in checks.items() if r.status_code != 200]
        if failed:
            print(f"SELFTEST FAILED: {', '.join(failed)}")
            return 1
        voice_problem = _voice_selftest()
        if voice_problem:
            print(f"SELFTEST FAILED: voice: {voice_problem}")
            return 1
        unauthenticated = httpx.get(f"{runtime.base_url}/v1/connections", timeout=10).status_code
        if unauthenticated != 401:
            print(f"SELFTEST FAILED: API accepted a request without the token ({unauthenticated})")
            return 1
        print(f"SELFTEST OK (GSOI {__version__})")
        return 0
    except Exception:
        traceback.print_exc()
        print("SELFTEST FAILED")
        return 1
    finally:
        runtime.stop()


def voice_test(seconds: float = 15.0) -> int:
    """Console diagnostic: speak "Hey Jarvis" and watch what the microphone and detector see."""
    from gsoi_desktop.voice.audio import MicrophoneError, SoundDeviceSource
    from gsoi_desktop.voice.diagnostics import VoiceTester, advise, format_report
    from gsoi_desktop.voice.wake import (
        OpenWakeWordDetector,
        VoiceUnavailableError,
        ensure_wake_models,
    )

    home = app_home()
    config = load_config(home)
    try:
        ensure_wake_models()
        detector = OpenWakeWordDetector(config.wake_threshold)
    except VoiceUnavailableError as exc:
        print(f"Voce non disponibile: {exc}")
        return 1

    def show(level: float, score: float, max_level: float, max_score: float) -> None:
        bar = "#" * int(level * 30)
        mark = "  <-- RICONOSCIUTO" if score >= config.wake_threshold else ""
        print(
            f"\rmic [{bar:<30}] parola {score:4.2f} (max {max_score:4.2f}){mark}      ",
            end="",
            flush=True,
        )

    print(f"Prova di {seconds:.0f} secondi: di' ben chiaro «Hey Jarvis» un paio di volte...")
    try:
        report = VoiceTester(
            SoundDeviceSource(config.microphone), detector, config.wake_threshold, show
        ).run(seconds)
    except MicrophoneError as exc:
        print(f"\n{exc}")
        return 1
    advice = advise(report)
    text = format_report(report, advice)
    print("\n\n" + text)
    (home / "logs").mkdir(exist_ok=True)
    (home / "logs" / "voice-test.txt").write_text(text, encoding="utf-8")
    return 0 if advice.ok else 2


def run_gui(minimized: bool, quit_after_ms: int | None = None) -> int:
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

    from gsoi_desktop.autostart import Autostart
    from gsoi_desktop.client import ApiClient
    from gsoi_desktop.controller import AssistantController
    from gsoi_desktop.ui.async_call import AsyncRunner
    from gsoi_desktop.ui.icon import make_icon
    from gsoi_desktop.ui.main_window import MainWindow
    from gsoi_desktop.ui.services_dialog import ServicesDialog
    from gsoi_desktop.ui.settings_dialog import SettingsDialog
    from gsoi_desktop.ui.speech import Speaker
    from gsoi_desktop.ui.tray import Tray
    from gsoi_desktop.ui.voice_bridge import VoiceBridge
    from gsoi_desktop.voice.audio import list_microphones
    from gsoi_desktop.voice.pipeline import VoiceState
    from gsoi_desktop.voice.service import VoiceService

    app = QApplication(sys.argv[:1])
    app.setApplicationName("GSOI")
    app.setOrganizationName("GSOI")
    app.setQuitOnLastWindowClosed(False)  # lives in the tray
    icon = make_icon()
    app.setWindowIcon(icon)

    home = app_home()
    lock = QLockFile(str(home / "gsoi.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        if not minimized:
            QMessageBox.information(
                QWidget(), "GSOI", "GSOI è già in esecuzione (cerca l'icona vicino all'orologio)."
            )
        return 0

    store = choose_secret_store(home)
    config = load_config(home)
    runtime = BackendRuntime(home, config, store)
    try:
        runtime.start()
    except Exception as exc:
        QMessageBox.critical(
            QWidget(),
            "GSOI",
            f"Non riesco ad avviare il servizio interno.\n\n{exc}\n\nLog: {home / 'logs'}",
        )
        return 1

    client = ApiClient(runtime.base_url, runtime.token)
    controller = AssistantController(client)
    runner = AsyncRunner()
    speaker = Speaker()
    autostart = Autostart()
    state = {"config": config}

    def configured() -> bool:
        return state["config"].is_configured(runtime.model_key_present)

    window = MainWindow(
        controller,
        runner,
        speaker,
        is_configured=configured,
        read_aloud=lambda: state["config"].read_aloud,
    )

    # ---- voice -----------------------------------------------------------------------------
    bridge = VoiceBridge()
    voice = VoiceService(home / "models", bridge)

    def on_voice_state(voice_state: object) -> None:
        if voice_state is VoiceState.LISTENING:
            window.show_listening(0.0)
        elif voice_state is VoiceState.TRANSCRIBING:
            window.show_idle()
            window.status.setText("Ti ho sentito, un attimo...")
        elif voice_state is VoiceState.WAITING:
            window.show_idle()
            window.status.setText("")

    def on_voice_heard(text: str) -> None:
        window.submit_voice(text)

    bridge.state_changed.connect(on_voice_state)
    bridge.level_changed.connect(window.set_voice_level)
    bridge.heard.connect(on_voice_heard)
    bridge.failed.connect(lambda message: window.add("error", message))
    bridge.progress.connect(
        lambda what, fraction: window.status.setText(
            f"{what} {int(fraction * 100)}%" if 0 < fraction < 1 else what
        )
    )
    window.voice_turn_finished.connect(voice.resume)
    window.microphone_toggled.connect(voice.trigger_or_cancel)

    def apply_voice(cfg: DesktopConfig) -> None:
        if not cfg.voice_enabled:
            voice.disable()
            window.set_voice_available(False)
            window.status.setText("")
            return

        def done(_: object) -> None:
            window.status.setText("")
            window.set_voice_available(True)

        def broke(error: Exception) -> None:
            window.status.setText("")
            window.add("error", f"Comando vocale non disponibile: {error}")

        runner.run(lambda: voice.enable(cfg, bridge.report_progress), done, broke)

    def open_voice_test(dialog: SettingsDialog) -> None:
        from gsoi_desktop.ui.voice_test_dialog import VoiceTestDialog, make_default_tester_factory
        from gsoi_desktop.voice.wake import (
            OpenWakeWordDetector,
            VoiceUnavailableError,
            ensure_wake_models,
        )

        try:
            ensure_wake_models()
            detector = OpenWakeWordDetector(dialog.wake_threshold.value())
        except VoiceUnavailableError as exc:
            window.add("error", f"Prova non disponibile: {exc}")
            return
        mic = dialog.mic.currentData()
        voice.pause()  # the test needs the microphone for itself
        try:
            tester = VoiceTestDialog(
                runner,
                make_default_tester_factory(mic, detector, dialog.wake_threshold.value()),
                dialog.wake_threshold.value(),
                dialog,
            )
            tester.exec()
            if tester.chosen_threshold is not None:
                dialog.wake_threshold.setValue(tester.chosen_threshold)
        finally:
            voice.unpause()

    def show_settings() -> None:
        dialog = SettingsDialog(
            state["config"],
            has_key=runtime.model_key_present,
            autostart_supported=autostart.supported,
            microphones=list_microphones,
            parent=window,
        )
        dialog.voice_test_requested.connect(lambda: open_voice_test(dialog))
        if dialog.exec() != SettingsDialog.DialogCode.Accepted or dialog.result_value is None:
            return
        result = dialog.result_value
        if result.new_api_key:
            store.set(secret_name(result.config.provider), result.new_api_key)
        save_config(home, result.config)
        autostart.set_enabled(result.config.autostart)
        state["config"] = result.config
        window.status.setText("Applico le impostazioni...")
        runner.run(lambda: runtime.restart(result.config), applied, failed)
        apply_voice(result.config)

    def applied(_: object) -> None:
        window.status.setText("")
        client_new = ApiClient(runtime.base_url, runtime.token)
        controller._api = client_new  # new port after the restart
        window.refresh_banner()

    def failed(error: Exception) -> None:
        window.status.setText("")
        window.add("error", f"Impossibile applicare le impostazioni: {error}")

    def show_services() -> None:
        ServicesDialog(controller, runner, window).exec()

    window.open_settings.connect(show_settings)
    window.open_services.connect(show_services)

    def quit_app() -> None:
        voice.disable()
        speaker.close()
        tray.hide()
        app.quit()

    tray = Tray(icon, window, on_briefing=window.request_briefing, on_quit=quit_app)
    window.hide_on_close = tray.available  # without a tray, closing really quits
    app.aboutToQuit.connect(runtime.stop)

    if config.voice_enabled:
        apply_voice(config)
    if config.autostart and autostart.supported and not autostart.is_enabled():
        autostart.set_enabled(True)  # keep the registry entry pointing at this executable

    if not (minimized and tray.available and configured()):
        window.show()
    if not configured() and quit_after_ms is None:  # a smoke test must not wait on a dialog
        show_settings()
    if quit_after_ms is not None:  # smoke test: start everything, then leave
        from PySide6.QtCore import QTimer

        QTimer.singleShot(quit_after_ms, app.quit)
    code = app.exec()
    lock.unlock()
    return int(code)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gsoi", description="GSOI Personal Assistant")
    parser.add_argument(
        "--selftest", action="store_true", help="start the backend headless and check it"
    )
    parser.add_argument("--minimized", action="store_true", help="start in the tray")
    parser.add_argument(
        "--gui-selftest", action="store_true", help="open the UI briefly, then exit"
    )
    parser.add_argument(
        "--voice-test", action="store_true", help="diagnose the microphone and the wake word"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.selftest:
        return selftest()
    if args.voice_test:
        return voice_test()
    if args.gui_selftest:
        code = run_gui(minimized=False, quit_after_ms=1500)
        return _record(None, code)
    return run_gui(args.minimized)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
