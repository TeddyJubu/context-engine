# Context Engine — Mac App Design Spec

**Date:** 2026-03-19
**Status:** Approved for implementation

---

## Overview

A standalone macOS application that runs the Context Engine HTTP server in the background and provides a windowed management UI. Users install it once, double-click to launch, and never touch the terminal again. The browser extension and Claude Code MCP continue to work exactly as before — the app simply replaces the manual `python server.py` step.

---

## Goals

- Eliminate the manual server startup step for end users
- Provide a native Mac window for managing collections
- Distribute as a self-contained `.app` bundle (no Python installation required)
- Leave `mcp_server.py` and the browser extension completely unchanged

## Non-Goals

- Replacing or rewriting the MCP server
- Providing a search UI within the app (Claude Code handles search)
- Cross-platform support (macOS only)
- Code-signing or App Store distribution (out of scope for v1)

---

## Architecture

The app is a single Python process with two concurrent layers:

```
┌──────────────────────────────────────────┐
│  Context Engine.app  (PyInstaller bundle) │
│                                           │
│  ┌────────────────┐   ┌────────────────┐  │
│  │  FastAPI server│   │  pywebview     │  │
│  │  (background   │◄──│  window        │  │
│  │   thread)      │   │  (HTML/CSS/JS) │  │
│  │  port 11811    │   │                │  │
│  └────────────────┘   └────────────────┘  │
│                                           │
│  ~/.context-engine/collections/ (on disk) │
└──────────────────────────────────────────┘
             ▲
             │ unchanged — still works
      Browser extension + mcp_server.py
```

**Startup sequence:**
1. `app.py` is the PyInstaller entry point
2. It starts `server.py`'s FastAPI app in a background thread via `uvicorn.run()`
3. It waits for the server to be ready (polls `/health`)
4. It opens a pywebview window loading bundled `ui/index.html`
5. The UI fetches data from `http://localhost:11811` directly

**Shutdown sequence:**
1. User closes the window or quits via Dock
2. pywebview triggers `on_closed` callback
3. `app.py` signals the uvicorn thread to stop
4. Process exits cleanly

---

## Components

### New Files

#### `app.py`
Entry point for the Mac app. Responsibilities:
- Start the FastAPI server in a daemon thread using `uvicorn.run()`
- Poll `GET /health` until the server responds (max 30s, then show error)
- Open the pywebview window (`title="Context Engine"`, fixed size ~780×520, no resizing needed)
- Handle window close to shut down the server thread
- Expose an `Api` class to pywebview's JS bridge for the Open at Login toggle

```python
# Skeleton
class Api:
    def set_open_at_login(self, enabled: bool) -> bool: ...
    def get_open_at_login(self) -> bool: ...

def start_server_thread(): ...
def wait_for_server(timeout=30): ...
def main(): ...
```

#### `ui/index.html`
Single-page management UI. Dark theme matching the browser extension (`#0f0f1a` background, `#4a9eff` accent). Three-tab sidebar layout:

- **Overview tab** (default): Server status dot (green/red), port number, uptime, total collections count, total document count
- **Collections tab**: Scrollable list of collections with name + doc count, "New Collection" button (prompts for name, calls `POST /collections`), delete button per row (calls `DELETE /collections/{name}` with confirmation)
- **Settings tab**: "Open at Login" toggle (calls `Api.set_open_at_login()`), embedding model name (read-only, from `GET /health`), data directory path (read-only)

#### `ui/style.css`
Dark theme stylesheet. Key design tokens:
- Background: `#0f0f1a` (page), `#16213e` (panels), `#12122a` (inputs/rows)
- Accent: `#4a9eff`
- Success: `#22c55e`
- Danger: `#ff5f57`
- Font: `-apple-system, BlinkMacSystemFont, sans-serif`
- Border radius: `8px` for panels, `5px` for buttons/inputs

#### `ui/app.js`
Vanilla JS (no framework). Responsibilities:
- On load: fetch `/collections` and `/health`, render Overview tab
- Tab switching via sidebar clicks
- Collections CRUD: fetch list, render rows, wire up New + Delete buttons
- Settings: call `window.pywebview.api.get_open_at_login()` on load, toggle calls `set_open_at_login()`
- Poll `/health` every 5s to keep status dot current

#### `ContextEngine.spec`
PyInstaller spec file. Key settings:
- `--windowed` (no terminal window)
- `--name "Context Engine"`
- `datas`: include `ui/` folder, bundled sentence-transformers model from cache
- `hiddenimports`: fastapi, uvicorn, zvec, sentence_transformers, pywebview

#### `build.sh`
```bash
#!/bin/bash
set -e
pip install pyinstaller pywebview
pyinstaller ContextEngine.spec
echo "Built: dist/Context Engine.app"
```

### Modified Files

#### `server.py`
Current `if __name__ == "__main__": uvicorn.run(...)` block stays. No functional changes needed — `app.py` imports and runs the `app` object directly via `uvicorn.run("server:app", ...)` in a thread.

One addition: expose a `shutdown_event` threading.Event so `app.py` can signal graceful shutdown.

#### `install.sh`
Add a note directing users to the `.app` bundle as the recommended path, with the existing `pip install` flow kept as the developer/CLI path.

#### `.gitignore`
Add:
```
dist/
build/
*.spec.bak
.superpowers/
```

### Unchanged Files
- `mcp_server.py` — no changes
- `extension/` — no changes
- `crawler.py` — no changes
- `connect.py` — no changes

---

## Data Flow

### Startup
```
app.py
  → thread: uvicorn.run("server:app", port=11811)
  → poll GET /health until 200
  → pywebview.create_window("Context Engine", "ui/index.html", js_api=Api())
  → pywebview.start()
```

### UI → API
All data operations go through the existing REST API:
```
ui/app.js  →  fetch("http://localhost:11811/...")  →  server.py handlers
```

### Open at Login
```
Settings toggle  →  window.pywebview.api.set_open_at_login(true)
  → app.py writes ~/Library/LaunchAgents/com.contextengine.app.plist
    pointing to the .app bundle's executable
  → launchctl load <plist>
```
Turning it off: `launchctl unload` + delete the plist.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Server fails to start in 30s | pywebview shows inline error page with log output |
| Port 11811 already in use | Error message in window: "Another instance may be running" |
| Collection delete fails | Alert in UI, list refreshes to current state |
| Server crashes mid-session | Status dot turns red; UI shows "Reconnecting…"; polls until healthy |

---

## Build & Distribution

```
# Developer build
bash build.sh
# → dist/Context Engine.app

# User install
# 1. Download Context Engine.app
# 2. Drag to /Applications
# 3. Double-click to launch
# 4. Enable "Open at Login" in Settings tab
```

PyInstaller bundles Python runtime, all pip dependencies, the sentence-transformers model, and the `ui/` folder. The resulting `.app` is self-contained — no Python installation required on the user's machine.

**Approximate bundle size:** 400–600 MB (dominated by the sentence-transformers model and PyTorch).

---

## Open Questions

- Should the app support running alongside a separately launched `server.py` (e.g., if a developer is already running it)? Current design: app always starts its own server; if port is in use it shows an error.
- Should the window remember its last position? (Nice to have, not in v1.)
- Code-signing: deferred. Users will need to right-click → Open to bypass Gatekeeper for unsigned builds.
