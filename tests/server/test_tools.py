"""The tool surface an agent sees: exactly four memory tools, and no fifth (FR-065).

Asserted over the transport the plugin actually runs — a subprocess speaking
MCP on stdin and stdout — rather than over the inventory object, because a tool
is exposed when the protocol answers with it. A fifth name reaching `tools/list`
is the defect FR-066 names, and only this seam can see it.
"""

from __future__ import annotations

import ast
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

import processrecall.server.mcp

pytestmark = pytest.mark.unit

#: FR-065's four, and nothing else: recall guidance, remember an annotation,
#: mark an outcome, inspect the graph.
EXPECTED_TOOLS = {"recall", "remember", "mark_outcome", "inspect"}

_SERVER = StdioServerParameters(
    command=sys.executable,
    args=["-m", "processrecall.server.mcp.stdio_server"],
)


async def _list_tools() -> list[types.Tool]:
    """Everything a client is offered after a real initialize handshake."""
    async with (
        stdio_client(_SERVER) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=5)) as session,
    ):
        await session.initialize()
        return (await session.list_tools()).tools


@pytest.fixture(scope="module")
def served_tools() -> list[types.Tool]:
    """One server spawn's tool list, shared by every test that reads it."""
    return asyncio.run(_list_tools())


def test_stdio_server_exposes_exactly_the_four_memory_tools(
    served_tools: list[types.Tool],
) -> None:
    """FR-065: the four are all there, and they are all there is."""
    served = {tool.name for tool in served_tools}
    assert served == EXPECTED_TOOLS


#: What each tool takes: every argument it publishes, and the subset a caller
#: must supply. `recall` without a procedure means the work's current position
#: (FR-065) and `mark_outcome` without a prompt means the current prompt
#: (FR-035), so neither name is required; `inspect` takes nothing at all.
EXPECTED_ARGUMENTS = {
    "recall": ({"procedure"}, set()),
    "remember": ({"edge", "note"}, {"edge", "note"}),
    "mark_outcome": ({"prompt_id", "outcome"}, {"outcome"}),
    "inspect": (set(), set()),
}


def test_each_tool_publishes_its_arguments_as_a_schema(served_tools: list[types.Tool]) -> None:
    """The argument models are the published contract: schema, not prose."""
    schemas = {tool.name: tool.inputSchema for tool in served_tools}
    published = {
        name: (set(schema.get("properties", {})), set(schema.get("required", [])))
        for name, schema in schemas.items()
    }
    assert all(schema["type"] == "object" for schema in schemas.values())
    assert published == EXPECTED_ARGUMENTS


#: The package's whole third-party budget, per file relative to `MCP_PACKAGE`:
#: the schema library where the argument models live, and the transport where
#: the server does. Every other import a file makes directly is the standard
#: library or this package's own code; that code's own transitive stdlib-only
#: contract is `.importlinter`'s to keep, not this test's.
THIRD_PARTY_BUDGET = {"arguments.py": {"pydantic"}, "stdio_server.py": {"mcp"}}

MCP_PACKAGE = Path(processrecall.server.mcp.__file__).parent


def _imported_roots(module: Path) -> set[str]:
    """Every top-level package *module* imports, relative imports aside."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_pydantic_stays_in_the_argument_models_and_every_other_file_stays_in_budget() -> None:
    """Read from the source, because an import is a fact about the file."""
    for module in sorted(MCP_PACKAGE.rglob("*.py")):
        dependencies = _imported_roots(module) - sys.stdlib_module_names - {"processrecall"}
        budget = THIRD_PARTY_BUDGET.get(module.relative_to(MCP_PACKAGE).as_posix(), set())
        assert dependencies <= budget, (
            f"{module.relative_to(MCP_PACKAGE)} imports {sorted(dependencies)}, beyond its budget"
        )
