# Context Engine

A local-only semantic search system that captures web documentation from Chrome and makes it searchable by VS Code coding agents via MCP.

No cloud, no subscriptions. Everything runs on your machine.

```
Chrome Extension  →  FastAPI Server (localhost:11811)  →  MCP Server  →  VS Code Agent
```

---

## Features

- **Chrome Extension** — one-click capture of any page or selection; crawl entire doc sites
- **YouTube transcripts** — optional popup tool to add the active YouTube video's captions locally through the browser session
- **Semantic search** — `BAAI/bge-base-en-v1.5` embeddings via sentence-transformers (768-dim, fully local)
- **Multi-collection store** — separate zvec index per topic (e.g. `raycast-docs`, `react-docs`)
- **Async BFS crawler** — polite, concurrent crawling of documentation sites
- **MCP server** — plug into Claude Code, Cursor, Copilot, Continue.dev — any MCP-compatible agent

---

## Architecture

```
Chrome Extension (Manifest V3)
  ├── Popup: collection picker, add page, crawl site
  ├── Content Script: extract page text / selection
  ├── Context Menu: right-click → add to collection
  └── Service Worker: relay to local server
         │ HTTP (localhost:11811)
         ▼
FastAPI Server (server.py)
  ├── Multi-collection zvec store (~/.context-engine/collections/{name}/)
  ├── sentence-transformers BAAI/bge-base-en-v1.5 (local, 768-dim)
  ├── Async BFS web crawler (httpx + beautifulsoup4)
  └── REST API
         │ stdio
         ▼
MCP Server (mcp_server.py)
  ├── tool: search_docs
  ├── tool: list_collections
  └── tool: add_memory
```

---

## File Structure

```
context-engine/
├── server.py          # FastAPI server (port 11811)
├── crawler.py         # Async BFS web crawler
├── mcp_server.py      # MCP stdio server for VS Code / Claude Code
├── requirements.txt
├── install.sh         # One-shot setup script
└── extension/         # Chrome Extension (Manifest V3)
    ├── manifest.json
    ├── popup.html
    ├── popup.css
    ├── popup.js
    ├── content.js
    ├── background.js
    └── icons/
```

---

## Setup

### 1. Install dependencies

```bash
git clone https://github.com/TeddyJubu/context-engine.git
cd context-engine
bash install.sh
```

This creates a `.venv/`, installs all Python deps, and prints next steps.

### 2. Start the server

```bash
.venv/bin/python3 server.py
```

Verify: `curl http://localhost:11811/health`

Most endpoints require an auth header:

```bash
export CONTEXT_TOKEN="$(cat ~/.context-engine/token)"
```

### 3. Load the Chrome extension

1. Go to `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked** → select the `extension/` folder

### 4. Connect to your coding agents

```bash
python3 connect.py
```

This auto-detects installed agents (Claude Code, Cursor, VS Code, Windsurf, Claude Desktop) and configures them. You can also connect specific agents:

```bash
python3 connect.py --claude-code --cursor
python3 connect.py --all
python3 connect.py --status    # check what's connected
python3 connect.py --all --remove    # disconnect all
```

<details>
<summary>Manual configuration</summary>

If you prefer manual setup, add to your agent's MCP config:

**Claude Code** (`~/.claude.json`):
```json
{
  "mcpServers": {
    "context-engine": {
      "command": "/path/to/context-engine/.venv/bin/python3",
      "args": ["/path/to/context-engine/mcp_server.py"]
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "context-engine": {
      "command": "/path/to/context-engine/.venv/bin/python3",
      "args": ["/path/to/context-engine/mcp_server.py"]
    }
  }
}
```

**VS Code / Copilot** (`~/.vscode/mcp.json`):
```json
{
  "servers": {
    "context-engine": {
      "command": "/path/to/context-engine/.venv/bin/python3",
      "args": ["/path/to/context-engine/mcp_server.py"]
    }
  }
}
```

**Windsurf** (`~/.codeium/windsurf/mcp_config.json`):
```json
{
  "mcpServers": {
    "context-engine": {
      "command": "/path/to/context-engine/.venv/bin/python3",
      "args": ["/path/to/context-engine/mcp_server.py"]
    }
  }
}
```

</details>

---

## API Reference

The server runs at `http://localhost:11811`.

`GET /health` is unauthenticated. Collection, add, search, and crawl endpoints require `X-Context-Token: <token>`.

| Method | Path | Body | Description |
|--------|------|------|-------------|
| `GET` | `/health` | — | Status, collection count, model name |
| `GET` | `/collections` | — | List all collections with doc counts |
| `POST` | `/collections` | `{name}` | Create a collection |
| `DELETE` | `/collections/{name}` | — | Delete a collection |
| `POST` | `/add` | `{text, collection, source?, tags?, source_type?, metadata?}` | Add text (auto-chunked) |
| `POST` | `/search` | `{query, collection?, top_k?, filter_tags?}` | Semantic search |
| `POST` | `/crawl` | `{url, collection, max_pages?, path_prefix?}` | Start async crawl |
| `GET` | `/crawl/{task_id}` | — | Poll crawl progress |

When `collection` is omitted in `/search`, all collections are searched and results merged by score.

### Quick examples

```bash
# Create a collection
curl -X POST localhost:11811/collections \
  -H "X-Context-Token: $CONTEXT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "raycast-docs"}'

# Add a snippet
curl -X POST localhost:11811/add \
  -H "X-Context-Token: $CONTEXT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"text": "Use getPreferenceValues() to read user preferences.", "collection": "raycast-docs", "source": "https://developers.raycast.com"}'

# Search
curl -X POST localhost:11811/search \
  -H "X-Context-Token: $CONTEXT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query": "how do I read user preferences", "collection": "raycast-docs"}'

# Crawl an entire doc site
curl -X POST localhost:11811/crawl \
  -H "X-Context-Token: $CONTEXT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://developers.raycast.com/basics/getting-started", "collection": "raycast-docs", "max_pages": 50}'

# Poll crawl status
curl localhost:11811/crawl/{task_id} \
  -H "X-Context-Token: $CONTEXT_TOKEN"
```

---

## MCP Tools

| Tool | Parameters | Description |
|------|-----------|-------------|
| `search_docs` | `query`, `collection?`, `top_k?` | Semantic search across indexed docs |
| `list_collections` | — | Show collections and doc counts |
| `add_memory` | `text`, `collection`, `source?` | Store a fact or note |

### Usage in an agent

```
search_docs("how to create a Raycast command", collection="raycast-docs")
list_collections()
add_memory("Always use useNavigation for stack-based navigation", collection="raycast-docs")
```

---

## Chrome Extension

The popup gives you full control:

- **Status badge** — shows connected, auth-needed, or offline state
- **Collection picker** — switch between collections or create new ones
- **Add this page** — extracts and indexes the current page's main content
- **Add selection** — indexes only the text you've highlighted
- **YouTube Transcript** — extracts captions from the active YouTube video tab and stores them as `youtube_transcript` context
- **Crawl site** — BFS crawl from the current URL up to N pages, with live progress

Right-click context menus:
- **Add selection to Context Engine** — on any selected text
- **Add page to Context Engine** — on any page

If the server asks for auth, paste the token from `~/.context-engine/token` into the popup's auth card and save it once.

### YouTube transcript notes

- Open a supported YouTube video tab, choose a collection, then click **Add transcript** in the popup.
- Transcript extraction stays local to the extension and uses the browser's live YouTube session.
- Successful items are stored with `source_type: youtube_transcript` and metadata including video id, title, URL, language, generated/manual caption status, and extraction method.

---

## Crawler Details

- Uses `httpx.AsyncClient` with a concurrency semaphore of 5
- Polite 0.5s delay between requests
- Extracts text from `<main>` → `<article>` → `<body>` (priority order)
- Strips `nav`, `footer`, `header`, `aside`, `script`, `style`, `svg`
- Follows only same-domain links within the specified path prefix
- Default `max_pages`: 200
- Each page is chunked with the recursive splitter (`2048` chars with `200` char overlap by default), embedded, and deduplicated before insert

---

## Data Storage

Collections are stored at `~/.context-engine/collections/{name}/` as zvec indexes.

Each document stores: `hash`, `text`, `source`, `tags`, `timestamp`, `source_type`, optional transcript metadata, and a 768-dim float32 embedding.

Deduplication is done by SHA-1 hash of the text — identical chunks are skipped on re-add.

---

## Requirements

- Python 3.10+
- ~500MB disk for the embedding model (downloaded on first run)
- Chrome / Chromium for the extension

```
fastapi>=0.110
uvicorn>=0.27
httpx>=0.27
beautifulsoup4>=4.12
sentence-transformers>=2.5
zvec
mcp>=1.0
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CONTEXT_ENGINE_DIR` | `~/.context-engine` | Data directory |
| `EMBED_MODEL` | `BAAI/bge-base-en-v1.5` | Sentence-transformers model |
| `CONTEXT_TOP_K` | `8` | Default search result count |
