"""The agent-facing tool surface: four memory tools over MCP stdio (FR-065).

`stdio_server` is the whole server — the declaration of what is exposed and the
transport that answers with it. `arguments` holds the four argument models, and
is the only module in the package that imports pydantic.
"""
