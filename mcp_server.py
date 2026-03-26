#!/usr/bin/env python3
"""
Context Engine — MCP stdio server for VS Code / Claude Code / Cursor
Thin wrapper that calls the HTTP API at localhost:11811.
"""

import httpx
from mcp.server.fastmcp import FastMCP
from context_engine_config import AUTH_HEADER, AUTH_TOKEN, DEFAULT_TOP_K, SERVER_URL

mcp = FastMCP("context-engine", instructions="Semantic search over locally indexed web documentation and notes.")

def _get(path: str) -> dict:
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{SERVER_URL}{path}", headers={AUTH_HEADER: AUTH_TOKEN})
        r.raise_for_status()
        return r.json()

def _post(path: str, body: dict) -> dict:
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{SERVER_URL}{path}", json=body, headers={AUTH_HEADER: AUTH_TOKEN})
        r.raise_for_status()
        return r.json()

@mcp.tool()
def search_docs(query: str, collection: str | None = None, top_k: int = DEFAULT_TOP_K) -> str:
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
        source_type = r.get("source_type")
        metadata = r.get("metadata") or {}
        score_str = f" (score: {score:.3f})" if score is not None else ""
        lines = [f"## Result {i}{score_str}", f"**Source:** {source}", f"**Collection:** {coll}"]
        if source_type:
            lines.append(f"**Type:** {source_type}")
        if source_type == "youtube_transcript":
            if metadata.get("title"):
                lines.append(f"**Title:** {metadata['title']}")
            if metadata.get("language"):
                lines.append(f"**Language:** {metadata['language']}")
            if metadata.get("method"):
                lines.append(f"**Method:** {metadata['method']}")
        lines.append("")
        lines.append(r["text"])
        parts.append("\n".join(lines))
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
