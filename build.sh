#!/bin/bash
set -e

echo "==> Installing build dependencies..."
pip install pyinstaller pywebview httpx

echo "==> Pre-downloading embedding model..."
python -c "
from sentence_transformers import SentenceTransformer
SentenceTransformer('all-MiniLM-L6-v2')
print('Model ready.')
"

echo "==> Running PyInstaller..."
pyinstaller ContextEngine.spec --noconfirm --clean

echo ""
echo "==> Build complete: dist/Context Engine.app"
echo "    Bundle size: $(du -sh 'dist/Context Engine.app' | cut -f1)"
echo ""
echo "NOTE: App is unsigned. First-launch users must right-click -> Open"
echo "      to bypass macOS Gatekeeper."
