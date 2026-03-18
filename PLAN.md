# Context Engine — Chrome Extension + Local Brain + MCP Server

## Context

Teddy wants to capture web documentation (Raycast docs, React docs, any site) from Chrome and make it semantically searchable by VS Code coding agents. This is a **local-only** system — no VPS, no subscriptions. The existing OpenClaw brain service (`/home/ubuntu/brain/server.py`) provides proven patterns for zvec + sentence-transformers that we'll adapt into a multi-collection architecture.

## Architecture

```
Chrome Extension (Manifest V3)
  ├── Popup: collection picker, add page, start crawl
  ├── Content Script: extract page text / selection
  ├── Context Menu: right-click → add to collection
  └── Service Worker: relay to local server
         │ HTTP (localhost:11811)
         ▼
Local Context Engine Server (Python FastAPI)
  ├── Multi-collection zvec store (~/.context-engine/collections/{name}/)
  ├── sentence-transformers (all-MiniLM-L6-v2, local, 384-dim)
  ├── Async web crawler (httpx + beautifulsoup4)
  └── REST API: /collections, /add, /search, /crawl
         │
         ▼ stdio
MCP Server (Python, wraps HTTP API)
  ├── tool: search_docs(query, collection?)
  ├── tool: list_collections()
  ├── tool: add_memory(text, collection, source?)
  └── Configured in .vscode/mcp.json
```

## File Structure

```
~/context-engine/
├── server.py              # FastAPI server (port 11811)
├── crawler.py             # Async BFS web crawler module
├── mcp_server.py          # MCP stdio server for VS Code
├── requirements.txt
├── extension/             # Chrome Extension (Manifest V3)
│   ├── manifest.json
│   ├── popup.html
│   ├── popup.css
│   ├── popup.js
│   ├── content.js
│   ├── background.js
│   └── icons/             # 16/48/128px icons
└── install.sh             # One-shot setup script
```

## Implementation Phases

### Phase 1: Server core (`server.py`)

Adapt from `/home/ubuntu/brain/server.py`. Key change: **multi-collection** instead of single index.

- **Collection manager**: lazy-open dict `{name: zvec.Collection}`. Each collection is a separate zvec index at `~/.context-engine/collections/{name}/`.
- **Reuse verbatim** from brain: zvec schema (hash/text/source/agent/tags/ts + embedding), `chunk_text()`, `fact_hash()`, `embed()`, dedup logic.

**Endpoints:**

| Method | Path | Body | Returns |
|--------|------|------|---------|
| `GET` | `/health` | — | `{status, collections_count, model}` |
| `GET` | `/collections` | — | `[{name, doc_count}]` |
| `POST` | `/collections` | `{name}` | `{name, status:"created"}` |
| `DELETE` | `/collections/{name}` | — | `{status:"deleted"}` |
| `POST` | `/add` | `{text, collection, source?, tags?}` | `{status, hash}` |
| `POST` | `/search` | `{query, collection?, top_k?, filter_tags?}` | `{results: [{text, source, score, tags}]}` |
| `POST` | `/crawl` | `{url, collection, max_pages?, path_prefix?}` | `{task_id, status:"started"}` |
| `GET` | `/crawl/{task_id}` | — | `{status, pages_crawled, pages_total}` |

When `collection` is omitted in `/search`, search all collections and merge by score.

### Phase 2: Crawler (`crawler.py`)

- `httpx.AsyncClient` + `beautifulsoup4`, BFS with visited set
- Concurrency: semaphore of 5, 0.5s polite delay between requests
- Extract text from `<main>`, `<article>`, or `<body>` (priority order); strip nav/footer/script/style
- Follow only `<a href>` under same domain + path prefix
- Default `max_pages`: 200
- Each page → `chunk_text()` → embed → insert into collection
- Track state in `_crawl_tasks` dict keyed by UUID, polled via `GET /crawl/{task_id}`

### Phase 3: MCP server (`mcp_server.py`)

Thin stdio wrapper using `mcp` Python SDK. Calls the HTTP server — does NOT load the embedding model itself.

**Tools:**

| Tool | Params | Description |
|------|--------|-------------|
| `search_docs` | `query: str, collection?: str, top_k?: int` | Semantic search across indexed docs |
| `list_collections` | — | Show available memory groups with doc counts |
| `add_memory` | `text: str, collection: str, source?: str` | Store a new fact |

**VS Code config (`.vscode/mcp.json`):**
```json
{
  "servers": {
    "context-engine": {
      "command": "python3",
      "args": ["~/context-engine/mcp_server.py"]
    }
  }
}
```

Works with Claude Code (`~/.claude.json` mcpServers), Cursor, Copilot, Continue.dev — any agent that supports MCP.

### Phase 4: Chrome Extension (`extension/`)

**Manifest V3** with permissions: `activeTab`, `contextMenus`, `storage`. Host permission: `http://localhost:11811/*`.

**Popup UI (`popup.html` + `popup.js`):**
- Server status indicator (green/red dot)
- Collection dropdown (populated from `GET /collections`) + "New" button
- "Add this page" button → content script extracts text → `POST /add`
- "Crawl this site" → input pre-filled with current URL → `POST /crawl` → polls progress

**Content Script (`content.js`):**
- On message `extract_page`: returns `{text, url, title}` from page DOM
- On message `extract_selection`: returns `window.getSelection().toString()`

**Service Worker (`background.js`):**
- Context menu: "Add selection to Context Engine" (on text select)
- Context menu: "Add page to Context Engine"
- Stores active collection in `chrome.storage.local`

### Phase 5: Setup (`install.sh` + `requirements.txt`)

```
# requirements.txt
fastapi>=0.110
uvicorn>=0.27
httpx>=0.27
beautifulsoup4>=4.12
sentence-transformers>=2.5
zvec
mcp>=1.0
```

`install.sh`: creates venv, installs deps, prints instructions for Chrome + VS Code setup.

## Key Reuse from Existing Code

- `/home/ubuntu/brain/server.py` — zvec schema, `chunk_text()`, `fact_hash()`, `embed()`, dedup pattern, FastAPI lifespan, collection open/create pattern
- zvec API: `create_and_open()`, `open()`, `collection.insert()`, `collection.query()`, `collection.optimize()`, `VectorQuery`, `Doc`

## Verification

1. **Server**: `python3 server.py` → `curl localhost:11811/health` → `POST /collections {name:"test"}` → `POST /add` → `POST /search`
2. **Crawler**: `POST /crawl {url:"https://developers.raycast.com/basics/getting-started", collection:"raycast-docs", max_pages:10}` → poll until done → `POST /search {query:"create a command", collection:"raycast-docs"}`
3. **MCP**: `echo '{"jsonrpc":"2.0","method":"tools/list","id":1}' | python3 mcp_server.py` → verify tools listed
4. **Chrome**: Load unpacked → open any docs site → click "Add this page" → verify fact appears in `POST /search`
5. **End-to-end**: In VS Code, open a project with `mcp.json` configured → ask coding agent "search_docs: how to create a Raycast command" → verify it returns indexed content
