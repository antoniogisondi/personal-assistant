"""PyInstaller entry point."""

import multiprocessing

from gsoi_desktop.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
