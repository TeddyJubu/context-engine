#!/usr/bin/env python3

import os
from pathlib import Path


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 65535) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < minimum or value > maximum:
        return default
    return value


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return default
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or default


DATA_DIR = Path(os.environ.get("CONTEXT_ENGINE_DIR", Path.home() / ".context-engine")).expanduser()
COLL_DIR = DATA_DIR / "collections"

MODEL_NAME = os.environ.get("EMBED_MODEL", "BAAI/bge-base-en-v1.5")
EMBED_DIM = _env_int("CONTEXT_ENGINE_EMBED_DIM", 768, minimum=64, maximum=4096)
DEFAULT_TOP_K = _env_int("CONTEXT_TOP_K", 8, minimum=1, maximum=100)

SERVER_HOST = os.environ.get("CONTEXT_ENGINE_HOST", "127.0.0.1")
SERVER_PORT = _env_int("CONTEXT_ENGINE_PORT", 11811)
SERVER_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"

AUTH_HEADER = "X-Context-Token"

TOKEN_FILE = DATA_DIR / "token"


def _load_or_create_token() -> str:
    env_token = os.environ.get("CONTEXT_ENGINE_TOKEN")
    if env_token:
        return env_token
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    import secrets
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
    return token


AUTH_TOKEN = _load_or_create_token()

CORS_ALLOWED_ORIGINS = _env_list(
    "CONTEXT_ENGINE_ALLOWED_ORIGINS",
    ["http://localhost", "http://127.0.0.1"],
)
CORS_ALLOW_ORIGIN_REGEX = os.environ.get(
    "CONTEXT_ENGINE_ALLOWED_ORIGIN_REGEX",
    r"^chrome-extension://[a-p]{32}$",
)

CHUNK_SIZE = _env_int("CONTEXT_ENGINE_CHUNK_SIZE", 2048, minimum=256, maximum=8192)
_CHUNK_OVERLAP_RAW = _env_int("CONTEXT_ENGINE_CHUNK_OVERLAP", 200, minimum=0, maximum=2048)
CHUNK_OVERLAP = min(_CHUNK_OVERLAP_RAW, CHUNK_SIZE - 1)

DEDUP_SIMILARITY_THRESHOLD = float(os.environ.get("CONTEXT_ENGINE_DEDUP_THRESHOLD", "0.95"))
