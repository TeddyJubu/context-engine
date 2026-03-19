"""Open at Login support via macOS LaunchAgent plist."""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

PLIST_PATH = Path.home() / "Library/LaunchAgents/com.contextengine.app.plist"


def _plist_contents() -> dict:
    return {
        "Label": "com.contextengine.app",
        "ProgramArguments": [str(sys.executable)],
        "RunAtLoad": True,
        "KeepAlive": False,
    }


class Api:
    """Pywebview JS bridge. Methods are callable from ui/app.js via window.pywebview.api.*"""

    def get_open_at_login(self) -> dict:
        return {"enabled": PLIST_PATH.exists()}

    def set_open_at_login(self, enabled: bool) -> dict:
        if enabled:
            PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(PLIST_PATH, "wb") as f:
                plistlib.dump(_plist_contents(), f)
            res = subprocess.run(
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST_PATH)],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                PLIST_PATH.unlink(missing_ok=True)
                raise RuntimeError(res.stderr or res.stdout or "launchctl bootstrap failed")
        else:
            res = subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST_PATH)],
                capture_output=True,
                text=True,
            )
            PLIST_PATH.unlink(missing_ok=True)
            if res.returncode != 0:
                raise RuntimeError(res.stderr or res.stdout or "launchctl bootout failed")
        return {"ok": True}
