"""The four memory tools, served over JSON-RPC on stdin and stdout (FR-065).

The inventory below is the exposed surface: four tools and no fifth. Adding a
name here is what exposing a tool means, which is why FR-066's graph editing —
add, delete, revise — is not here but stays a programmatic interface that
`graph/` callers reach directly.

Each tool's handler is one module under `tools/`, named after the tool, imported
when a call for it arrives: the declaration a client lists is separable from the
work a call does, and each handler stays one file with one responsibility. That
package lands in T054-T057, one handler per task; this task only declares the
surface, so `tools/call` is not yet exercised.

Usage::

    processrecall-mcp    # or: python -m processrecall.server.mcp.stdio_server
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol

try:
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only on broken installs
    from processrecall.exceptions import BootstrapError

    raise BootstrapError(
        "mcp",
        "The MCP stdio server cannot start without it — re-run bootstrap to rebuild "
        "the plugin environment.",
    ) from exc

from processrecall.server.mcp.arguments import (
    InspectArguments,
    MarkOutcomeArguments,
    RecallArguments,
    RememberArguments,
)

#: Where a tool's handler lives: one module per tool, named after it.
_HANDLERS = "processrecall.server.mcp.tools"


class ArgumentModel(Protocol):
    """What the server asks of an argument model: a schema, and parsing.

    Stated structurally so the transport never has to import the library the
    models are written in.
    """

    @classmethod
    def model_json_schema(cls) -> dict[str, Any]:
        """The arguments as JSON schema, for the tool list."""

    @classmethod
    def model_validate(cls, payload: Mapping[str, Any]) -> Any:
        """*payload* as this tool's arguments, or a rejection."""


@dataclass(frozen=True)
class ToolSpec:
    """One exposed tool: its name, what it is for, and what it takes."""

    name: str
    summary: str
    arguments: type[ArgumentModel]

    def declare(self) -> types.Tool:
        """The tool as a client lists it."""
        return types.Tool(
            name=self.name,
            description=self.summary,
            inputSchema=self.arguments.model_json_schema(),
        )

    def call(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Run the handler over *payload*, parsed into this tool's arguments."""
        handler = getattr(import_module(f"{_HANDLERS}.{self.name}"), self.name)
        result: dict[str, Any] = handler(self.arguments.model_validate(payload))
        return result


#: FR-065's four, in the order an agent meets them: read, annotate, judge, look.
TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "recall",
        "Guidance for a named procedure, or for where the work already is.",
        RecallArguments,
    ),
    ToolSpec(
        "remember",
        "Attach a note to one move in the graph, so the next run reads it.",
        RememberArguments,
    ),
    ToolSpec(
        "mark_outcome",
        "Declare how a piece of work turned out, beside the derived outcome.",
        MarkOutcomeArguments,
    ),
    ToolSpec(
        "inspect",
        "The graph in counts: nodes, edges, conditions, annotations, counters.",
        InspectArguments,
    ),
)

_BY_NAME: Mapping[str, ToolSpec] = {spec.name: spec for spec in TOOLS}

server: Server[object, Any] = Server(
    "processrecall-memory",
    instructions=(
        "Procedural memory of how work on this project has actually gone: "
        "recall guidance before a step, remember what a move is worth, "
        "mark_outcome when the work lands, inspect what has been learned."
    ),
)


@server.list_tools()
async def _list_tools() -> list[types.Tool]:
    """Answer `tools/list` with the inventory, and nothing besides it."""
    return [spec.declare() for spec in TOOLS]


@server.call_tool()
async def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Answer `tools/call` from the named tool's handler."""
    return _BY_NAME[name].call(arguments)


async def _serve() -> None:
    """Serve until the client closes stdin."""
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    """Run the server: JSON-RPC on stdin and stdout, nothing on a port."""
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
