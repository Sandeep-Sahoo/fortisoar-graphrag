"""MCP server (stdio) so Claude Desktop / Claude Code can query the graph.

Run: python -m app.mcp_server
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .services import get_services

mcp = FastMCP("fortisoar-graphrag")


@mcp.tool()
def search_vendor_docs(query: str, k: int = 6) -> list[dict]:
    """Hybrid search over Fortinet FortiSOAR product documentation."""
    return get_services().search_vendor_docs(query, k)


@mcp.tool()
def search_internal_docs(query: str, k: int = 6) -> list[dict]:
    """Hybrid search over the company's internal SOC / playbook documentation."""
    return get_services().search_internal_docs(query, k)


@mcp.tool()
def search_company_playbooks(query: str, k: int = 5) -> list[dict]:
    """Find deployed company playbooks by meaning, name, step, connector or module."""
    return get_services().search_company_playbooks(query, k)


@mcp.tool()
def get_playbook(name_or_uuid: str) -> dict:
    """Full detail of one playbook: trigger, ordered steps, connector operations, calls, callers, docs."""
    return get_services().get_playbook(name_or_uuid)


@mcp.tool()
def get_collection(name: str) -> dict:
    """A playbook collection with its playbooks and the internal and vendor docs linked to them."""
    return get_services().get_collection(name)


@mcp.tool()
def find_dependents(name_or_uuid: str, depth: int = 3) -> dict:
    """Playbooks that call the given playbook, directly or transitively (impact analysis)."""
    return get_services().find_dependents(name_or_uuid, depth)


@mcp.tool()
def run_cypher(query: str) -> list[dict] | dict:
    """Read-only Cypher against the graph. Labels: Playbook, PlaybookCollection, Step, Connector,
    ConnectorOperation, Module, Document, Chunk, Concept. Rels: CONTAINS, HAS_STEP, NEXT, USES, OF,
    CALLS, TRIGGERED_ON, DOCUMENTED_BY (playbook/collection -> internal doc), REFERENCES (playbook/collection
    -> vendor doc), HAS_CHUNK, EXPLAINS."""
    return get_services().run_cypher(query)


@mcp.tool()
def ask(question: str) -> dict:
    """Answer a FortiSOAR question with citations, preferring company playbooks over vendor docs."""
    return get_services().ask(question)


if __name__ == "__main__":
    mcp.run()
