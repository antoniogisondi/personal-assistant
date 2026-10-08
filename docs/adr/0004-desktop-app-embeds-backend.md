# ADR 0004 - The desktop app embeds the backend

Status: accepted

The product is installed on each user's PC. The desktop program (PySide6) starts the same FastAPI
backend in-process on a random loopback port and talks to it over HTTP, like any other client.

Why: one codebase and one API (a future web or mobile client reuses it), the UI cannot bypass the
authorization layer, and security properties (tool policy, approvals, audit) are the ones already
tested. Secrets live in the OS credential store (Windows Credential Manager via `keyring`); the
local API token and the encryption key are generated on first run. Architecture rules enforce that
the backend never imports the desktop package and that non-UI desktop logic never imports Qt.

PySide6 (LGPL) rather than PyQt6 (GPL/commercial): the two bindings cannot be mixed in one program.
