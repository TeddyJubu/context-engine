#!/usr/bin/env bash
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
mkdir -p ~/.context-engine/collections

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
