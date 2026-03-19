"""Context Engine — Mac App entry point."""
import os
import sys
import threading
import time
from typing import Optional

import httpx
import webview

SERVER_PORT = 11811
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"

# Shared state between main thread and server thread
uvicorn_server = None
server_error: Optional[Exception] = None


def resource_path(relative: str) -> str:
    """Resolve a path to a bundled resource.

    In dev mode: relative to this file's directory.
    In PyInstaller bundle: relative to sys._MEIPASS.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


def error_html(message: str) -> str:
    """Return a self-contained HTML error page string (no server needed)."""
    safe = (message
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))
    return (
        "<html><body style='background:#0f0f1a;color:#ff5f57;"
        "font-family:-apple-system,BlinkMacSystemFont,sans-serif;"
        "display:flex;align-items:center;justify-content:center;"
        "height:100vh;margin:0;'>"
        "<div style='text-align:center;max-width:500px;padding:32px;'>"
        "<h2 style='margin-bottom:12px;'>Could not start Context Engine</h2>"
        f"<pre style='color:#aaa;font-size:12px;text-align:left;background:#16213e;"
        f"padding:16px;border-radius:8px;overflow:auto;'>{safe}</pre>"
        "</div></body></html>"
    )


def wait_for_server(timeout: float = 90, interval: float = 2) -> bool:
    """Poll GET /health until server responds or timeout/error occurs.

    Returns True on success, False on timeout or if server_error is set.
    """
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


def _server_thread_target():
    """Run uvicorn in a thread. Sets uvicorn_server global so shutdown() can stop it."""
    global uvicorn_server, server_error
    import uvicorn
    from server import app as fastapi_app
    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=SERVER_PORT, log_level="warning")
    uvicorn_server = uvicorn.Server(config)
    try:
        uvicorn_server.run()
    except Exception as e:
        server_error = e


def shutdown():
    """Called by pywebview's on_closed callback."""
    if uvicorn_server:
        uvicorn_server.should_exit = True


def main():
    t = threading.Thread(target=_server_thread_target, daemon=True)
    t.start()

    if not wait_for_server():
        err = str(server_error) if server_error else "Server did not respond within 90 seconds."
        webview.create_window("Context Engine — Error", html=error_html(err),
                              width=540, height=320)
        webview.start()
        sys.exit(1)

    from login_item import Api
    api = Api()
    webview.create_window(
        "Context Engine",
        url=resource_path("ui/index.html"),
        js_api=api,
        width=780,
        height=520,
        resizable=False,
    )
    webview.start(on_closed=shutdown)


if __name__ == "__main__":
    main()
