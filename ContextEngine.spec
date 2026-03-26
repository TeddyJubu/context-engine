# ContextEngine.spec
import shutil
from pathlib import Path

MODEL_NAME = "all-MiniLM-L6-v2"
hf_cache   = Path.home() / ".cache" / "huggingface" / "hub"
model_dir  = hf_cache / ("models--sentence-transformers--" + MODEL_NAME)

if not model_dir.exists():
    raise FileNotFoundError(
        "Model not found at " + str(model_dir) + ". Run: "
        "python -c \"from sentence_transformers import SentenceTransformer; "
        "SentenceTransformer('" + MODEL_NAME + "')\""
    )

# HuggingFace Hub stores model files as symlinks (snapshots/ -> blobs/).
# PyInstaller cannot copy symlinked trees, so we resolve them into a staging
# directory of real files before bundling.
staging_dir = Path("build/model_staging/models--sentence-transformers--" + MODEL_NAME)
if staging_dir.exists():
    shutil.rmtree(staging_dir)
staging_dir.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree(str(model_dir), str(staging_dir), symlinks=False)

block_cipher = None

a = Analysis(
    ["app.py"],
    pathex=[str(Path(".").resolve())],
    binaries=[],
    datas=[
        ("ui",               "ui"),
        (str(staging_dir),   "sentence_transformers_cache/models--sentence-transformers--" + MODEL_NAME),
        ("server.py",        "."),
        ("login_item.py",    "."),
        ("crawler.py",       "."),
    ],
    hiddenimports=[
        "fastapi", "fastapi.routing", "fastapi.middleware.cors",
        "uvicorn", "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
        "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan", "uvicorn.lifespan.on",
        "sentence_transformers", "sentence_transformers.models",
        "torch", "transformers",
        "zvec",
        "webview", "webview.platforms.cocoa",
        "httpx", "httpx._transports.default",
        "pydantic", "anyio", "anyio._backends._asyncio",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ContextEngine",
    debug=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=True, name="ContextEngine")

app = BUNDLE(
    coll,
    name="Context Engine.app",
    icon=None,
    bundle_identifier="com.contextengine.app",
    info_plist={
        "NSHighResolutionCapable": True,
        "LSUIElement": False,
    },
)
