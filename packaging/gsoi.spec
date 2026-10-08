# PyInstaller spec for the GSOI desktop app.   Build:  pyinstaller packaging/gsoi.spec
# Produces dist/GSOI/GSOI.exe (+ support files). A folder build starts faster and triggers
# antivirus false positives far less often than a single self-extracting file.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

root = Path(SPECPATH).parent

hiddenimports = (
    collect_submodules("gsoi_assistant")
    + collect_submodules("gsoi_desktop")
    + collect_submodules("uvicorn")
    + collect_submodules("sqlalchemy.dialects.sqlite")
    + collect_submodules("keyring.backends")
    + collect_submodules("sse_starlette")
    + ["aiosqlite", "tzdata", "alembic.runtime.migration", "alembic.ddl.impl", "yaml"]
)
datas = (
    [(str(root / "migrations"), "migrations")]  # database migrations run at startup
    + collect_data_files("gsoi_assistant")  # default authorization policy, etc.
    + collect_data_files("tzdata")  # IANA time zones on Windows
)
# --- voice: native libraries and model files must travel inside the app -------------------------
from gsoi_desktop.voice.wake import ensure_wake_models  # noqa: E402

ensure_wake_models()  # the wake-word models (a few MB) are bundled, not downloaded by users
voice_datas, voice_binaries, voice_hidden = [], [], []
for pkg in ("openwakeword", "faster_whisper", "ctranslate2", "onnxruntime", "sounddevice", "av", "tokenizers"):
    d, b, h = collect_all(pkg)
    voice_datas += d
    voice_binaries += b
    voice_hidden += h
try:  # PortAudio is shipped as a separate data package on Windows/macOS
    d, b, h = collect_all("_sounddevice_data")
    voice_datas += d
    voice_binaries += b
except Exception:
    pass
datas += voice_datas
hiddenimports += voice_hidden

excludes = ["asyncpg", "tkinter", "PySide6.QtWebEngineCore", "PySide6.QtQuick", "PySide6.Qt3DCore"]

a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(root / "src")],
    binaries=voice_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GSOI",
    console=False,  # a windowed app: no black console box
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="GSOI")
