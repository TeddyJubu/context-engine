#!/usr/bin/env bash
# ── Quick Install (recommended for end users) ─────────────────────────────────
# 1. Download Context Engine.app from the releases page
# 2. Drag to /Applications
# 3. Right-click -> Open (first launch only -- bypasses Gatekeeper for unsigned app)
# 4. Enable "Open at Login" in the Settings tab
#
# The app bundles the Python runtime and sentence-transformers model.
# No Python installation required.
#
# ── Developer / CLI Install ───────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "=== Context Engine Setup ==="

# Create virtualenv
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtualenv..."
    python3 -m venv "$VENV_DIR"
fi

echo "Installing dependencies..."
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"

# Create data directory
DATA_DIR="${CONTEXT_ENGINE_DIR:-$HOME/.context-engine}"
TOKEN_FILE="$DATA_DIR/token"
mkdir -p "$DATA_DIR/collections"

# Generate auth token if it doesn't exist
if [ -n "${CONTEXT_ENGINE_TOKEN:-}" ]; then
    printf '%s\n' "$CONTEXT_ENGINE_TOKEN" > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE" 2>/dev/null || true
    echo "Using auth token from CONTEXT_ENGINE_TOKEN."
elif [ ! -s "$TOKEN_FILE" ]; then
    echo "Generating auth token..."
    python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE" 2>/dev/null || true
    echo "  Token saved to $TOKEN_FILE"
fi

# Generate placeholder icons if they don't exist
if [ ! -f "$SCRIPT_DIR/extension/icons/icon16.png" ]; then
    echo "Generating extension icons..."
    "$VENV_DIR/bin/python3" -c "
from PIL import Image, ImageDraw, ImageFont
import sys

for size in [16, 48, 128]:
    img = Image.new('RGBA', (size, size), (99, 102, 241, 255))
    draw = ImageDraw.Draw(img)
    # Draw a simple 'C' letter
    fs = max(size // 2, 8)
    try:
        draw.text((size * 0.25, size * 0.15), 'C', fill='white')
    except Exception:
        pass
    img.save(f'$SCRIPT_DIR/extension/icons/icon{size}.png')
print('Icons generated.')
" 2>/dev/null || echo "  (Pillow not available — please add icon PNGs manually to extension/icons/)"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Start the server:"
echo "  $VENV_DIR/bin/python3 $SCRIPT_DIR/server.py"
echo ""
echo "Chrome Extension:"
echo "  1. Go to chrome://extensions"
echo "  2. Enable Developer mode"
echo "  3. Click 'Load unpacked' → select $SCRIPT_DIR/extension/"
echo ""
echo "VS Code / Claude Code MCP:"
echo '  Add to .vscode/mcp.json or ~/.claude.json:'
echo "  {\"servers\": {\"context-engine\": {\"command\": \"$VENV_DIR/bin/python3\", \"args\": [\"$SCRIPT_DIR/mcp_server.py\"]}}}"
echo ""
echo "Test:"
echo "  curl http://localhost:11811/health"
echo ""
echo "Auth token:"
echo "  cat $TOKEN_FILE"
echo "  (paste this into the Chrome extension when prompted)"

echo ""
echo "=== Connect to Coding Agents ==="
echo ""
read -p "Auto-configure MCP for your coding agents? [Y/n] " answer
if [ "$answer" != "n" ] && [ "$answer" != "N" ]; then
    "$VENV_DIR/bin/python3" "$SCRIPT_DIR/connect.py"
else
    echo "You can run this later: python3 connect.py"
fi
