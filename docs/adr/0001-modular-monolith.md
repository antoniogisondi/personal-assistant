# ADR 0001 — Modular monolith with ports & adapters

Status: accepted

One deployable Python application (API + worker share the codebase), with strict module
boundaries enforced by `import-linter` (see `pyproject.toml`). Layering:
`api → agent → llm → security|observability|db → config → core`. Connectors and providers are
adapters behind Protocols. We split into services only when a dependency forces it (voice, MCP).
