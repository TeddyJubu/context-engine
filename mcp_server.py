#!/usr/bin/env python3
"""
Context Engine — MCP stdio server for VS Code / Claude Code / Cursor
Thin wrapper that calls the HTTP API at localhost:11811.
"""

import httpx
from mcp.server.fastmcp import FastMCP

SERVER_URL = "http://127.0.0.1:11811"

mcp = FastMCP("context-engine", instructions="Semantic search over locally indexed web documentation and notes.")

def _get(path: str) -> dict:
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{SERVER_URL}{path}")
        r.raise_for_status()
        return r.json()

def _post(path: str, body: dict) -> dict:
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{SERVER_URL}{path}", json=body)
        r.raise_for_status()
        return r.json()

@mcp.tool()
def search_docs(query: str, collection: str | None = None, top_k: int = 8) -> str:
    """Semantic search across indexed documentation and notes.

    Args:
        query: Natural language search query
        collection: Optional collection name to search (searches all if omitted)
        top_k: Number of results to return (default 8)
    """
    body = {"query": query, "top_k": top_k}
    if collection:
        body["collection"] = collection
    data = _post("/search", body)
    results = data.get("results", [])
    if not results:
        return "No results found."
    parts = []
    for i, r in enumerate(results, 1):
        source = r.get("source", "unknown")
        coll = r.get("collection", "")
        score = r.get("score")
        score_str = f" (score: {score:.3f})" if score is not None else ""
        parts.append(f"## Result {i}{score_str}\n**Source:** {source}\n**Collection:** {coll}\n\n{r['text']}")
    return "\n\n---\n\n".join(parts)

@mcp.tool()
def list_collections() -> str:
    """List all available document collections with their doc counts."""
    data = _get("/collections")
    if not data:
        return "No collections found."
    lines = []
    for c in data:
        lines.append(f"- **{c['name']}**: {c['doc_count']} documents")
    return "\n".join(lines)

@mcp.tool()
def add_memory(text: str, collection: str, source: str = "manual") -> str:
    """Store a new fact or note in a collection.

    Args:
        text: The text content to store
        collection: Collection name to store in (created if it doesn't exist)
        source: Optional source URL or description
    """
    data = _post("/add", {"text": text, "collection": collection, "source": source})
    return f"Status: {data.get('status')}, chunks: {data.get('chunks', 1)}, added: {data.get('added', 0)}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
