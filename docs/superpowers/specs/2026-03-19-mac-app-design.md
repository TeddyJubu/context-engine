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

**Port is a compile-time constant (11811).** The UI hardcodes `http://localhost:11811`. This matches the existing extension and MCP. If port-binding fails, the app shows an error and exits rather than retrying on a different port.

### Startup Sequence

```
app.py
  │
  ├─ 1. Create uvicorn.Server(config) and store as global
  ├─ 2. Start server.serve() in a daemon thread
  ├─ 3. Poll GET /health every 2s (see: wait_for_server)
  │       ├─ success → continue
  │       └─ timeout (90s) → open error window and exit
  ├─ 4. pywebview.create_window("Context Engine", url="ui/index.html", js_api=Api())
  └─ 5. pywebview.start(on_closed=shutdown)   ← blocks until window closes
```

**Why 90 seconds:** On first launch, `sentence_transformers` downloads and caches the model. Subsequent launches are fast (~2–5s). The timeout must accommodate the slow first-run case.

**Polling interval:** 2 seconds. If the server thread raises an exception before the poll succeeds, the exception is captured via `threading.excepthook` or by checking a shared `server_error: Optional[Exception]` variable each poll iteration. If `server_error` is set, the poll loop exits immediately with that error rather than waiting for timeout.

### Shutdown Sequence

```
User closes window
  │
  └─ on_closed callback fires
       ├─ uvicorn_server.should_exit = True   ← signals uvicorn's internal loop
       ├─ server_thread.join(timeout=5)
       └─ sys.exit(0)
```

`uvicorn.Server` exposes `should_exit: bool`. Setting it to `True` causes uvicorn's `serve()` coroutine to exit cleanly on its next loop tick. This is the correct mechanism — do not use `thread.daemon` alone or `os.kill`.

---

## Components

### New Files

#### `app.py`

```python
import threading, time, sys, httpx, webview
from server import app as fastapi_app   # imports the FastAPI object
import uvicorn

SERVER_PORT = 11811
SERVER_URL  = f"http://127.0.0.1:{SERVER_PORT}"

uvicorn_server: uvicorn.Server = None
server_error: Exception = None

class Api:
    def set_open_at_login(self, enabled: bool) -> dict: ...
    def get_open_at_login(self) -> dict: ...

def server_thread_target():
    global uvicorn_server, server_error
    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=SERVER_PORT, log_level="warning")
    uvicorn_server = uvicorn.Server(config)
    try:
        uvicorn_server.run()          # blocks until should_exit=True
    except Exception as e:
        server_error = e

def wait_for_server(timeout=90, interval=2) -> bool:
    """Poll /health. Returns True on success, False on timeout or server error."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server_error:
            return False
        try:
            r = httpx.get(f"{SERVER_URL}/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False

def shutdown():
    if uvicorn_server:
        uvicorn_server.should_exit = True

def main():
    t = threading.Thread(target=server_thread_target, daemon=True)
    t.start()

    if not wait_for_server():
        err = str(server_error) if server_error else "Server did not respond within 90 seconds."
        # Open an error window using load_html (no server needed)
        w = webview.create_window("Context Engine — Error", html=error_html(err))
        webview.start()
        sys.exit(1)

    api = Api()
    webview.create_window(
        "Context Engine",
        url=resource_path("ui/index.html"),  # resolves correctly inside .app bundle
        js_api=api,
        width=780, height=520,
        resizable=False,
    )
    webview.start(on_closed=shutdown)

def resource_path(relative: str) -> str:
    """Resolve path to bundled resource. Works both in dev and inside PyInstaller .app."""
    import os
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)

def error_html(message: str) -> str:
    return f"""<html><body style="background:#0f0f1a;color:#ff5f57;font-family:-apple-system,sans-serif;
    display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
    <div style="text-align:center"><h2>Could not start server</h2><pre style="color:#aaa;font-size:12px">{message}</pre></div>
    </body></html>"""

if __name__ == "__main__":
    main()
```

**Key note on `resource_path`:** PyInstaller extracts bundled files to `sys._MEIPASS` at runtime. All file references in `app.py` must go through `resource_path()`. The `ui/` folder is referenced as `resource_path("ui/index.html")`.

#### `ui/index.html`

Single-page app. Three-tab sidebar. Dark theme matching the extension.

Structure:
```html
<body>
  <div id="sidebar">   <!-- Overview | Collections | Settings nav items -->
  <div id="content">   <!-- Tab panels, one shown at a time -->
</body>
```

On DOMContentLoaded, `app.js` is loaded. The pywebview bridge is **not** available immediately — `app.js` must wait for the `pywebviewready` event before calling any `window.pywebview.api.*` methods:

```js
window.addEventListener('pywebviewready', () => {
    // safe to call window.pywebview.api here
    initSettings();
});
```

All other API calls (fetch to localhost:11811) are safe to make before `pywebviewready`.

**Overview tab content:** Status dot (green = server responding, red = not), port number, uptime (computed from a start timestamp stored in `sessionStorage`), collections count, document count. Populated by `GET /health` and `GET /collections`.

**Collections tab content:** Scrollable list rendered from `GET /collections`. Each row: collection name, doc count, delete button. Delete button shows a `confirm()` dialog then calls `DELETE /collections/{name}`. "New Collection" button shows a `prompt()` for the name then calls `POST /collections`.

**Settings tab content:** "Open at Login" toggle (a styled checkbox). On load, calls `window.pywebview.api.get_open_at_login()`. On change, calls `window.pywebview.api.set_open_at_login(checked)`. Model name and data directory path from `GET /health` (read-only text).

#### `ui/style.css`

Design tokens:
```css
:root {
  --bg-page:    #0f0f1a;
  --bg-panel:   #16213e;
  --bg-input:   #12122a;
  --accent:     #4a9eff;
  --success:    #22c55e;
  --danger:     #ff5f57;
  --text:       #e0e0ff;
  --text-muted: #6666aa;
  --radius-lg:  8px;
  --radius-sm:  5px;
  --font:       -apple-system, BlinkMacSystemFont, sans-serif;
}
```

#### `ui/app.js`

Vanilla JS, no build step. Key responsibilities:

```js
// Tab switching
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => switchTab(item.dataset.tab));
});

// Health polling — every 5s, update status dot
setInterval(pollHealth, 5000);

// pywebview bridge — only for Settings tab
window.addEventListener('pywebviewready', initSettings);
```

The `start_ts` for uptime is set to `Date.now()` on first successful `/health` response and stored in `sessionStorage`.

#### `ContextEngine.spec`

```python
# ContextEngine.spec
import os
from sentence_transformers import SentenceTransformer

# Locate the cached model so PyInstaller can bundle it
MODEL_NAME = "all-MiniLM-L6-v2"
model_instance = SentenceTransformer(MODEL_NAME)  # triggers download if needed
model_cache_path = model_instance._model_card_data.base_model   # resolve cache dir

# The actual cache dir is in ~/.cache/huggingface/hub/
# Use sentence_transformers.util to find it:
from sentence_transformers import util as st_util
import torch
model_dir = os.path.join(
    os.path.expanduser("~"), ".cache", "huggingface", "hub",
    f"models--sentence-transformers--{MODEL_NAME}"
)

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('ui', 'ui'),                    # (src, dest_inside_bundle)
        (model_dir, f'sentence_transformers_cache/models--sentence-transformers--{MODEL_NAME}'),
    ],
    hiddenimports=[
        'fastapi', 'uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'sentence_transformers', 'torch', 'transformers',
        'zvec', 'pywebview', 'pywebview.platforms.cocoa',
        'httpx',
    ],
    hookspath=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ContextEngine', windowed=True)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name='ContextEngine')
app = BUNDLE(coll, name='Context Engine.app', icon=None, bundle_identifier='com.contextengine.app')
```

**Runtime model path resolution in `server.py`:** Add a helper that checks `sys._MEIPASS` first:

```python
# In server.py, replace the MODEL_NAME direct usage with:
def get_model_cache_dir():
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "sentence_transformers_cache")
    return None  # use default HuggingFace cache

# Pass to SentenceTransformer:
embedder = SentenceTransformer(MODEL_NAME, cache_folder=get_model_cache_dir())
```

#### `build.sh`

```bash
#!/bin/bash
set -e
echo "Installing build deps..."
pip install pyinstaller pywebview

echo "Pre-downloading model (required for bundling)..."
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

echo "Building .app..."
pyinstaller ContextEngine.spec --noconfirm

echo "Done: dist/Context Engine.app"
echo "Note: App is unsigned. Users must right-click → Open on first launch."
```

### Modified Files

#### `server.py`

Two additions only:

1. Import `sys` and add `get_model_cache_dir()` helper (see above), use it when instantiating `SentenceTransformer`.
2. The `if __name__ == "__main__"` block is unchanged — `server.py` still works standalone for developers.

No other changes. The `app` FastAPI object at module level is imported directly by `app.py`.

#### `install.sh`

Add a section above the existing pip-based instructions:

```
## Quick Install (recommended)
Download Context Engine.app, drag to /Applications, launch.
Enable "Open at Login" in the Settings tab.

## Developer / CLI Install
...existing instructions...
```

#### `.gitignore`

Add:
```
dist/
build/
*.spec.bak
.superpowers/
```

### Unchanged Files

`mcp_server.py`, `extension/`, `crawler.py`, `connect.py` — no changes.

---

## Open at Login — Implementation Detail

This is a macOS LaunchAgent. The `Api` class in `app.py` implements it as follows:

```python
import subprocess, plistlib
from pathlib import Path

PLIST_PATH = Path.home() / "Library/LaunchAgents/com.contextengine.app.plist"
APP_EXECUTABLE = Path(sys.executable)  # inside .app bundle when running packaged

def _plist_contents() -> dict:
    return {
        "Label": "com.contextengine.app",
        "ProgramArguments": [str(APP_EXECUTABLE)],
        "RunAtLoad": True,
        "KeepAlive": False,
    }

class Api:
    def set_open_at_login(self, enabled: bool) -> dict:
        if enabled:
            PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(PLIST_PATH, "wb") as f:
                plistlib.dump(_plist_contents(), f)
            subprocess.run(["launchctl", "bootstrap",
                            f"gui/{os.getuid()}", str(PLIST_PATH)], check=False)
        else:
            subprocess.run(["launchctl", "bootout",
                            f"gui/{os.getuid()}", str(PLIST_PATH)], check=False)
            PLIST_PATH.unlink(missing_ok=True)
        return {"ok": True}

    def get_open_at_login(self) -> dict:
        return {"enabled": PLIST_PATH.exists()}
```

**`launchctl bootstrap/bootout` vs `load/unload`:** macOS 10.15+ (Catalina and later) deprecated `launchctl load/unload`. Use `bootstrap gui/<uid> <plist>` and `bootout gui/<uid> <plist>` instead.

**`ProgramArguments`:** Must point to the executable inside the `.app` bundle. When running via PyInstaller, `sys.executable` is the bundled binary. This is correct for the LaunchAgent.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Server fails to respond within 90s | `error_html()` is passed to `webview.create_window(html=...)` — no server needed to show this |
| Port 11811 already in use | `server_error` is set immediately by uvicorn; polling loop catches it and shows error window |
| Collection delete fails (HTTP error) | `fetch()` catches non-2xx; shows `alert()` with the error message; re-fetches collection list |
| Server crashes mid-session | Next `/health` poll fails; status dot turns red; UI shows "Reconnecting…" banner; continues polling |
| Model download fails at first launch | uvicorn/sentence_transformers raises before `/health` responds; caught as `server_error`; shown in error window |

---

## Build & Distribution

```bash
# One-time developer build
bash build.sh
# → dist/Context Engine.app

# User install
# 1. Download Context Engine.app
# 2. Drag to /Applications
# 3. Right-click → Open (first launch only, bypasses Gatekeeper for unsigned app)
# 4. Enable "Open at Login" in the Settings tab
```

**Approximate bundle size:** 400–600 MB (sentence-transformers model + PyTorch dominate).

**Code-signing:** Deferred. Not required for local personal use. For distribution, add `codesign` + `notarytool` steps to `build.sh` in a future iteration.

---

## Open Questions (resolved)

- **Port conflicts:** App shows an error and exits. It does not attempt to use an alternate port.
- **Window position memory:** Not in v1. Window always opens at default position.
- **Running alongside a dev server.py:** Not supported. If port is in use, error is shown.
