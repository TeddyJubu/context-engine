# Context Engine: Reliability & Safety Implementation Plan

> **Purpose**: Step-by-step instructions for a coding agent to implement 7 priority changes.
> Each task lists exact files, line numbers, current code, and replacement code.
> Tasks MUST be executed in order — later tasks depend on earlier ones.

---

## TASK 1: Replace `chunk_text` with recursive splitting (512 tokens, overlap)

### Goal
Replace the naive 400-character paragraph splitter with a recursive character splitter at ~512 tokens (~2048 chars) with ~10% overlap (~200 chars). This is the benchmark-validated default (FloTorch 2026: 69% accuracy, best across all strategies).

### Why not use langchain or chonkie?
Neither is in `requirements.txt`. Keep zero new dependencies — implement the recursive splitter directly. It is a simple algorithm.

### Files to modify

#### `server.py` — Replace `chunk_text` function (currently lines 101-113)

**Current code:**
```python
def chunk_text(text: str, size: int = 400) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, current = [], ""
    for p in paragraphs:
        if len(current) + len(p) < size:
            current = (current + "\n\n" + p).strip()
        else:
            if current:
                chunks.append(current)
            current = p
    if current:
        chunks.append(current)
    return chunks or [text[:size]]
```

**Replace with:**
```python
def chunk_text(text: str, size: int = 2048, overlap: int = 200) -> list[str]:
    """Recursive character splitter. Tries separators in order of preference:
    double-newline > single-newline > space > hard cut.
    Size and overlap are in characters (~4 chars per token).
    Default: ~512 tokens chunk, ~50 tokens overlap.
    """
    separators = ["\n\n", "\n", " "]

    def _split(text: str, seps: list[str]) -> list[str]:
        if len(text) <= size:
            return [text] if text.strip() else []

        sep = ""
        for s in seps:
            if s in text:
                sep = s
                break

        if not sep:
            # Hard character split as last resort
            chunks = []
            for i in range(0, len(text), size - overlap):
                chunk = text[i:i + size].strip()
                if chunk:
                    chunks.append(chunk)
            return chunks

        parts = text.split(sep)
        remaining_seps = seps[seps.index(sep) + 1:] if sep in seps else []

        chunks = []
        current = ""
        for part in parts:
            candidate = (current + sep + part).strip() if current else part.strip()
            if len(candidate) <= size:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                if len(part) > size:
                    # Recurse with finer separators
                    chunks.extend(_split(part.strip(), remaining_seps))
                    current = ""
                else:
                    current = part.strip()
        if current:
            chunks.append(current)

        return chunks

    raw_chunks = _split(text, separators)

    if not raw_chunks:
        return [text[:size]] if text.strip() else []

    if overlap <= 0 or len(raw_chunks) <= 1:
        return raw_chunks

    # Apply overlap: prepend the tail of the previous chunk to the current one
    result = [raw_chunks[0]]
    for i in range(1, len(raw_chunks)):
        prev = raw_chunks[i - 1]
        overlap_text = prev[-overlap:] if len(prev) > overlap else prev
        # Find a clean break point (space) in the overlap prefix
        space_idx = overlap_text.find(" ")
        if space_idx > 0:
            overlap_text = overlap_text[space_idx + 1:]
        combined = (overlap_text + " " + raw_chunks[i]).strip()
        result.append(combined)

    return result
```

#### `context_engine_config.py` — Add chunk config constants

**Add at the end of the file (after line 38):**
```python
CHUNK_SIZE = _env_int("CONTEXT_ENGINE_CHUNK_SIZE", 2048, minimum=256, maximum=8192)
CHUNK_OVERLAP = _env_int("CONTEXT_ENGINE_CHUNK_OVERLAP", 200, minimum=0, maximum=2048)
```

#### `server.py` — Import new config values

**Current import block (lines 27-37):**
```python
from context_engine_config import (
    AUTH_HEADER,
    AUTH_TOKEN,
    COLL_DIR,
    CORS_ALLOWED_ORIGINS,
    CORS_ALLOW_ORIGIN_REGEX,
    DEFAULT_TOP_K,
    EMBED_DIM,
    MODEL_NAME,
    SERVER_HOST,
    SERVER_PORT,
)
```

**Replace with:**
```python
from context_engine_config import (
    AUTH_HEADER,
    AUTH_TOKEN,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COLL_DIR,
    CORS_ALLOWED_ORIGINS,
    CORS_ALLOW_ORIGIN_REGEX,
    DEFAULT_TOP_K,
    EMBED_DIM,
    MODEL_NAME,
    SERVER_HOST,
    SERVER_PORT,
)
```

#### `server.py` — Update the `/add` endpoint to use config values

**Current code (line 272):**
```python
    chunks = chunk_text(req.text)
```

**Replace with:**
```python
    chunks = chunk_text(req.text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
```

#### `crawler.py` — Remove `from server import chunk_text` and accept it as a parameter

This fixes the tight coupling. The crawler should receive `chunk_text` as a callable, just like it already receives `embed_fn` and `add_fn`.

**Current code in `crawl_site` function signature (lines 63-72):**
```python
async def crawl_site(
    url: str,
    collection,
    task_state: dict,
    max_pages: int = 200,
    path_prefix: str | None = None,
    embed_fn=None,
    add_fn=None,
):
```

**Replace with:**
```python
async def crawl_site(
    url: str,
    collection,
    task_state: dict,
    max_pages: int = 200,
    path_prefix: str | None = None,
    embed_fn=None,
    add_fn=None,
    chunk_fn=None,
):
```

**Current code inside the crawl loop (lines 109-113):**
```python
            # Chunk and add
            from server import chunk_text
            chunks = chunk_text(text, size=400)
            for chunk in chunks:
                add_fn(chunk, collection, source=current_url)
```

**Replace with:**
```python
            # Chunk and add
            chunks = chunk_fn(text)
            for chunk in chunks:
                add_fn(chunk, collection, source=current_url)
```

#### `server.py` — Pass `chunk_fn` to `crawl_site` in the `/crawl` endpoint

**Current code (lines 286-294):**
```python
            await crawl_site(
                url=req.url,
                collection=coll,
                task_state=_crawl_tasks[task_id],
                max_pages=req.max_pages,
                path_prefix=req.path_prefix,
                embed_fn=embed,
                add_fn=add_to_collection,
            )
```

**Replace with:**
```python
            await crawl_site(
                url=req.url,
                collection=coll,
                task_state=_crawl_tasks[task_id],
                max_pages=req.max_pages,
                path_prefix=req.path_prefix,
                embed_fn=embed,
                add_fn=add_to_collection,
                chunk_fn=lambda text: chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP),
            )
```

### Verification
After this change:
- `crawler.py` must have ZERO imports from `server.py`
- `chunk_text("short text")` returns `["short text"]` (no splitting for text under 2048 chars)
- `chunk_text("x" * 5000)` returns multiple chunks, each <= 2048 chars, with overlapping content between consecutive chunks
- The overlap text starts at a word boundary (space), not mid-word

---

## TASK 2: Switch embedding model to `BAAI/bge-base-en-v1.5` (768-dim)

### Goal
Replace `all-MiniLM-L6-v2` (384-dim, 78.1% top-5 accuracy) with `BAAI/bge-base-en-v1.5` (768-dim, 84.7% top-5 accuracy).

### CRITICAL WARNING
Changing the model makes ALL existing vector collections incompatible. Existing collections will need to be deleted and re-indexed. This is acceptable at this stage, but log a warning at startup if existing collections are found with mismatched dimensions.

### Files to modify

#### `context_engine_config.py` — Change model defaults

**Current code (lines 25-26):**
```python
MODEL_NAME = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")
EMBED_DIM = 384
```

**Replace with:**
```python
MODEL_NAME = os.environ.get("EMBED_MODEL", "BAAI/bge-base-en-v1.5")
EMBED_DIM = _env_int("CONTEXT_ENGINE_EMBED_DIM", 768, minimum=64, maximum=4096)
```

**IMPORTANT**: `EMBED_DIM` must match the model. `BAAI/bge-base-en-v1.5` outputs 768-dim vectors. If the user overrides `EMBED_MODEL` via env var, they must also set `CONTEXT_ENGINE_EMBED_DIM` to match. The config should NOT auto-detect — keep it explicit and fast.

#### `server.py` — Add dimension mismatch warning at startup

**In the `lifespan` function, after the model is loaded (after line ~226 `log.info("Embedding model loaded.")`), add:**

```python
    # Verify model dimension matches config
    test_vec = embedder.encode(["test"], normalize_embeddings=True)[0]
    actual_dim = len(test_vec)
    if actual_dim != EMBED_DIM:
        log.error(
            "EMBED_DIM mismatch: config says %d but model '%s' produces %d-dim vectors. "
            "Set CONTEXT_ENGINE_EMBED_DIM=%d or change models. Exiting.",
            EMBED_DIM, MODEL_NAME, actual_dim, actual_dim,
        )
        raise SystemExit(1)
```

### Verification
- Server starts and logs `Loading embedding model: BAAI/bge-base-en-v1.5`
- `curl http://localhost:11811/health` returns `"model": "BAAI/bge-base-en-v1.5"`
- If you temporarily set `EMBED_DIM` to 384 but keep the new model, the server refuses to start with a clear error

---

## TASK 3: Add `embed_model` field to zvec schema

### Goal
Track which embedding model produced each stored vector. This enables future model migration without silent data corruption.

### Files to modify

#### `server.py` — Update `make_schema` function

**Current schema fields (lines 51-58):**
```python
        fields=[
            zvec.FieldSchema(name="hash",   data_type=zvec.DataType.STRING, nullable=False),
            zvec.FieldSchema(name="text",   data_type=zvec.DataType.STRING, nullable=False),
            zvec.FieldSchema(name="source", data_type=zvec.DataType.STRING, nullable=True),
            zvec.FieldSchema(name="agent",  data_type=zvec.DataType.STRING, nullable=True),
            zvec.FieldSchema(name="tags",   data_type=zvec.DataType.ARRAY_STRING, nullable=True),
            zvec.FieldSchema(name="ts",     data_type=zvec.DataType.INT64, nullable=True),
        ],
```

**Replace with:**
```python
        fields=[
            zvec.FieldSchema(name="hash",        data_type=zvec.DataType.STRING, nullable=False),
            zvec.FieldSchema(name="text",        data_type=zvec.DataType.STRING, nullable=False),
            zvec.FieldSchema(name="source",      data_type=zvec.DataType.STRING, nullable=True),
            zvec.FieldSchema(name="agent",       data_type=zvec.DataType.STRING, nullable=True),
            zvec.FieldSchema(name="tags",        data_type=zvec.DataType.ARRAY_STRING, nullable=True),
            zvec.FieldSchema(name="ts",          data_type=zvec.DataType.INT64, nullable=True),
            zvec.FieldSchema(name="embed_model", data_type=zvec.DataType.STRING, nullable=True),
        ],
```

#### `server.py` — Update `add_to_collection` to store the model name

**Current doc creation in `add_to_collection` (lines ~150-160):**
```python
    doc = zvec.Doc(
        id=h,
        vectors={"embedding": vec},
        fields={
            "hash":   h,
            "text":   text,
            "source": source,
            "agent":  "context-engine",
            "tags":   tags,
            "ts":     int(time.time()),
        },
    )
```

**Replace with:**
```python
    doc = zvec.Doc(
        id=h,
        vectors={"embedding": vec},
        fields={
            "hash":        h,
            "text":        text,
            "source":      source,
            "agent":       "context-engine",
            "tags":        tags,
            "ts":          int(time.time()),
            "embed_model": MODEL_NAME,
        },
    )
```

### Impact on existing collections
Existing collections have a different schema (no `embed_model` field). Since we changed the embedding model in Task 2, existing collections are already incompatible. The user should delete old collections and re-index. The new schema will only apply to newly created collections.

### Verification
- Create a new collection and add text to it
- Verify the stored documents include the `embed_model` field by checking via the search endpoint (add `embed_model` to the search result fields — see below)

#### `server.py` — Include `embed_model` in search results

**In the search endpoint, current result item construction (approx lines 259-266):**
```python
            item = {
                "text":       r.fields.get("text", ""),
                "source":     r.fields.get("source", ""),
                "tags":       r.fields.get("tags", []),
                "score":      r.score if hasattr(r, "score") else None,
                "collection": cname,
            }
```

**Replace with:**
```python
            item = {
                "text":        r.fields.get("text", ""),
                "source":      r.fields.get("source", ""),
                "tags":        r.fields.get("tags", []),
                "score":       r.score if hasattr(r, "score") else None,
                "collection":  cname,
                "embed_model": r.fields.get("embed_model", ""),
            }
```

---

## TASK 4: Generate auth token randomly at install time

> Historical implementation note: this task was planned against an earlier revision. Treat the code blocks below as reference examples only. Current acceptance criteria are that `CONTEXT_ENGINE_CONFIG.AUTH_TOKEN` defaults to an empty string in the extension, `background.js` and `popup.js` hydrate `authToken` from `chrome.storage.local`, and the popup provides a user path to save and validate the token before authenticated requests run.

### Goal
Replace the hardcoded `"context-engine-local-token"` with a randomly generated token stored in `~/.context-engine/token`. The token file is created once during install and read by all components.

### Files to modify

#### `context_engine_config.py` — Token loading logic

**Current code (line 33):**
```python
AUTH_TOKEN = os.environ.get("CONTEXT_ENGINE_TOKEN", "context-engine-local-token")
```

**Replace with:**
```python
TOKEN_FILE = DATA_DIR / "token"


def _load_or_create_token() -> str:
    env_token = os.environ.get("CONTEXT_ENGINE_TOKEN")
    if env_token:
        return env_token
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    # Generate a new token
    import secrets
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
    # Restrict file permissions to owner only (Unix)
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
    return token


AUTH_TOKEN = _load_or_create_token()
```

**IMPORTANT**: `DATA_DIR` is defined on line 22 and is used here. The `TOKEN_FILE` line must come AFTER the `DATA_DIR` definition.

#### `install.sh` — Generate token during setup

**After the line `mkdir -p ~/.context-engine/collections` (line 18), add:**
```bash
# Generate auth token if it doesn't exist
TOKEN_FILE="$HOME/.context-engine/token"
if [ ! -f "$TOKEN_FILE" ]; then
    echo "Generating auth token..."
    python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
    echo "  Token saved to $TOKEN_FILE"
fi
```

#### `extension/config.js` — Remove hardcoded token, instruct user to set it

**Current code:**
```javascript
const CONTEXT_ENGINE_CONFIG = {
  API_BASE: "http://localhost:11811",
  AUTH_HEADER: "X-Context-Token",
  AUTH_TOKEN: "context-engine-local-token",
};
```

**Replace with:**
```javascript
const CONTEXT_ENGINE_CONFIG = {
  API_BASE: "http://localhost:11811",
  AUTH_HEADER: "X-Context-Token",
  AUTH_TOKEN: "",  // Set via extension options or loaded from server
};
```

#### `extension/background.js` — Load token from chrome.storage on startup

**Current code (lines 3-7):**
```javascript
importScripts("config.js");

const API_BASE = CONTEXT_ENGINE_CONFIG.API_BASE;
const AUTH_HEADER = CONTEXT_ENGINE_CONFIG.AUTH_HEADER;
const AUTH_TOKEN = CONTEXT_ENGINE_CONFIG.AUTH_TOKEN;
```

**Replace with:**
```javascript
importScripts("config.js");

let API_BASE = CONTEXT_ENGINE_CONFIG.API_BASE;
let AUTH_HEADER = CONTEXT_ENGINE_CONFIG.AUTH_HEADER;
let AUTH_TOKEN = CONTEXT_ENGINE_CONFIG.AUTH_TOKEN;

// Load saved token from storage (set during initial popup connection)
chrome.storage.local.get(["authToken"], (data) => {
  if (data.authToken) {
    AUTH_TOKEN = data.authToken;
  }
});
```

#### `extension/popup.js` — Load token from storage, add a token input in the first-run flow

**Current code (lines 1-11):**
```javascript
// Context Engine — Popup Script

const CONFIG = globalThis.CONTEXT_ENGINE_CONFIG || {
  API_BASE: "http://localhost:11811",
  AUTH_HEADER: "X-Context-Token",
  AUTH_TOKEN: "context-engine-local-token",
};

const API = CONFIG.API_BASE;
const AUTH_HEADER = CONFIG.AUTH_HEADER;
const AUTH_TOKEN = CONFIG.AUTH_TOKEN;
```

**Replace with:**
```javascript
// Context Engine — Popup Script

const CONFIG = globalThis.CONTEXT_ENGINE_CONFIG || {
  API_BASE: "http://localhost:11811",
  AUTH_HEADER: "X-Context-Token",
  AUTH_TOKEN: "",
};

let API = CONFIG.API_BASE;
let AUTH_HEADER = CONFIG.AUTH_HEADER;
let AUTH_TOKEN = CONFIG.AUTH_TOKEN;

// Load persisted token
(async () => {
  const stored = await chrome.storage.local.get(["authToken"]);
  if (stored.authToken) {
    AUTH_TOKEN = stored.authToken;
  }
})();
```

#### `server.py` — Add a `/token-check` endpoint so the extension can validate its token

**Add this new endpoint after the `/health` endpoint:**
```python
@app.post("/token-check")
def token_check(_auth: None = Depends(require_write_token)):
    return {"status": "valid"}
```

#### `install.sh` — Print the token location at the end

**In the final output section, add after the "Test:" line:**
```bash
echo ""
echo "Auth token:"
echo "  cat ~/.context-engine/token"
echo "  (paste this into the Chrome extension when prompted)"
```

### Verification
- Delete `~/.context-engine/token` if it exists
- Run `bash install.sh` — a new random token is created at `~/.context-engine/token`
- The token file has `0600` permissions
- Start server — it reads the token from the file
- `curl -X POST http://localhost:11811/token-check` returns 401
- `curl -X POST http://localhost:11811/token-check -H "X-Context-Token: $(cat ~/.context-engine/token)"` returns `{"status": "valid"}`
- If `CONTEXT_ENGINE_TOKEN` env var is set, that takes priority over the file

---

## TASK 5: Authenticate read endpoints

> Historical implementation note: this task was planned against an earlier revision. The stable requirement is that authenticated reads use `X-Context-Token`, `/health` stays unauthenticated, and popup/background/MCP clients send the current token rather than relying on hardcoded values.

### Goal
Require the auth token on `GET /collections` and `POST /search` to prevent local data exfiltration. Keep `GET /health` unauthenticated (it leaks no user data).

### Files to modify

#### `server.py` — Add a `require_read_token` dependency (or reuse `require_write_token`)

For simplicity, reuse the same token for reads and writes. This can be split later.

**Current `list_collections` endpoint:**
```python
@app.get("/collections")
def list_collections():
```

**Replace with:**
```python
@app.get("/collections")
def list_collections(_auth: None = Depends(require_write_token)):
```

**Current `search` endpoint:**
```python
@app.post("/search")
def search(req: SearchRequest):
```

**Replace with:**
```python
@app.post("/search")
def search(req: SearchRequest, _auth: None = Depends(require_write_token)):
```

**Current `crawl_status` endpoint:**
```python
@app.get("/crawl/{task_id}")
def crawl_status(task_id: str):
```

**Replace with:**
```python
@app.get("/crawl/{task_id}")
def crawl_status(task_id: str, _auth: None = Depends(require_write_token)):
```

#### `mcp_server.py` — Send auth token on ALL requests (including reads)

**Current `_get` function:**
```python
def _get(path: str) -> dict:
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{SERVER_URL}{path}")
        r.raise_for_status()
        return r.json()
```

**Replace with:**
```python
def _get(path: str) -> dict:
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{SERVER_URL}{path}", headers={AUTH_HEADER: AUTH_TOKEN})
        r.raise_for_status()
        return r.json()
```

**Current `_post` function:**
```python
def _post(path: str, body: dict, auth_required: bool = False) -> dict:
    headers = {AUTH_HEADER: AUTH_TOKEN} if auth_required else None
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{SERVER_URL}{path}", json=body, headers=headers)
        r.raise_for_status()
        return r.json()
```

**Replace with (always send auth):**
```python
def _post(path: str, body: dict) -> dict:
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{SERVER_URL}{path}", json=body, headers={AUTH_HEADER: AUTH_TOKEN})
        r.raise_for_status()
        return r.json()
```

Then update the one call site that passes `auth_required=True`:

**Current (in `add_memory` tool):**
```python
    data = _post("/add", {"text": text, "collection": collection, "source": source}, auth_required=True)
```

**Replace with:**
```python
    data = _post("/add", {"text": text, "collection": collection, "source": source})
```

#### `extension/popup.js` — Send auth token on ALL fetch calls

The popup already sends auth headers on write calls via the `authHeaders()` helper. But `loadCollections()` and `checkServer()` do NOT send auth headers.

**Current `checkServer` function (approx line 100):**
```javascript
async function checkServer() {
  try {
    const r = await fetch(`${API}/health`);
```

This one is fine — `/health` stays unauthenticated.

**Current `loadCollections` function (approx line 115):**
```javascript
  try {
    const r = await fetch(`${API}/collections`);
```

**Replace with:**
```javascript
  try {
    const r = await fetch(`${API}/collections`, {
      headers: authHeaders(),
    });
```

### Verification
- `curl http://localhost:11811/collections` returns 401
- `curl http://localhost:11811/collections -H "X-Context-Token: $(cat ~/.context-engine/token)"` returns the list
- `curl -X POST http://localhost:11811/search -H "Content-Type: application/json" -d '{"query":"test"}'` returns 401
- `curl http://localhost:11811/health` still returns 200 (no auth needed)
- MCP tools still work (they now send auth on all requests)
- Extension popup still loads collections (it sends auth headers)

---

## TASK 6: Add chunk overlap and HTML-aware splitting for crawled content

### Goal
When crawling HTML documentation, extract text preserving header structure and use it to create better chunk boundaries. The crawler already extracts from `<main>` / `<article>` / `<body>`. Enhance `extract_text` in `crawler.py` to preserve heading markers so the recursive splitter can use them.

### Files to modify

#### `crawler.py` — Enhance `extract_text` to preserve heading structure

**Current `extract_text` function (lines 16-30):**
```python
def extract_text(html: str) -> str:
    """Extract clean text from HTML, preferring main/article content."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove unwanted tags
    for tag in soup.find_all(STRIP_TAGS):
        tag.decompose()

    # Try main content areas first
    content = soup.find("main") or soup.find("article") or soup.find("body")
    if content is None:
        return ""

    text = content.get_text(separator="\n", strip=True)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
```

**Replace with:**
```python
def extract_text(html: str) -> str:
    """Extract clean text from HTML, preserving heading structure as markdown-style markers.
    Headings become double-newline separated sections, which the recursive splitter
    will use as natural chunk boundaries.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(STRIP_TAGS):
        tag.decompose()

    content = soup.find("main") or soup.find("article") or soup.find("body")
    if content is None:
        return ""

    # Insert double-newlines before headings so they become separator points
    for heading in content.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        level = int(heading.name[1])
        prefix = "#" * level
        heading_text = heading.get_text(strip=True)
        if heading_text:
            heading.replace_with(f"\n\n{prefix} {heading_text}\n\n")

    # Convert <pre> and <code> blocks to preserve formatting
    for pre in content.find_all(["pre"]):
        code_text = pre.get_text()
        pre.replace_with(f"\n\n```\n{code_text}\n```\n\n")

    text = content.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
```

This means the recursive splitter (from Task 1) will encounter `\n\n` before each heading and will naturally split there first, keeping each section together.

### Verification
- Crawl any docs site (e.g., a simple HTML page with `<h2>` sections)
- Verify that chunks tend to start with heading markers (`## Section Name`)
- Verify chunks do not split mid-heading-section unless the section itself exceeds the chunk size

---

## TASK 7: Add near-duplicate detection at ingest

### Goal
Augment the current exact-match SHA-1 dedup with a lightweight cosine similarity check against existing content. If a new chunk's embedding is >0.95 cosine similarity with an existing chunk from the same source URL, skip it.

### Why 0.95 threshold?
Versioned docs pages with minor edits (typo fixes, small additions) produce near-identical embeddings. Exact SHA-1 misses these. 0.95 cosine similarity catches them without risking false positives on genuinely different content.

### Files to modify

#### `context_engine_config.py` — Add dedup threshold config

**Add at the end of the file:**
```python
DEDUP_SIMILARITY_THRESHOLD = float(os.environ.get("CONTEXT_ENGINE_DEDUP_THRESHOLD", "0.95"))
```

#### `server.py` — Import the new config

**Add `DEDUP_SIMILARITY_THRESHOLD` to the import block from `context_engine_config`:**
```python
from context_engine_config import (
    AUTH_HEADER,
    AUTH_TOKEN,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COLL_DIR,
    CORS_ALLOWED_ORIGINS,
    CORS_ALLOW_ORIGIN_REGEX,
    DEDUP_SIMILARITY_THRESHOLD,
    DEFAULT_TOP_K,
    EMBED_DIM,
    MODEL_NAME,
    SERVER_HOST,
    SERVER_PORT,
)
```

#### `server.py` — Modify `add_to_collection` to check embedding similarity

**Current `add_to_collection` function:**
```python
def add_to_collection(text: str, coll: zvec.Collection, source: str = "manual", tags: list[str] = []) -> dict:
    h = fact_hash(text)
    try:
        existing = coll.query(filter=f'hash == "{h}"', topk=1)
        if existing:
            return {"status": "duplicate", "hash": h}
    except Exception:
        pass

    vec = embed(text)
    doc = zvec.Doc(
        id=h,
        vectors={"embedding": vec},
        fields={
            "hash":        h,
            "text":        text,
            "source":      source,
            "agent":       "context-engine",
            "tags":        tags,
            "ts":          int(time.time()),
            "embed_model": MODEL_NAME,
        },
    )
    coll.insert(doc)
    return {"status": "added", "hash": h}
```

**Replace with:**
```python
def add_to_collection(text: str, coll: zvec.Collection, source: str = "manual", tags: list[str] = []) -> dict:
    h = fact_hash(text)
    # Exact duplicate check
    try:
        existing = coll.query(filter=f'hash == "{h}"', topk=1)
        if existing:
            return {"status": "duplicate", "hash": h}
    except Exception:
        pass

    vec = embed(text)

    # Near-duplicate check via embedding similarity
    if DEDUP_SIMILARITY_THRESHOLD < 1.0 and coll_doc_count(coll) > 0:
        try:
            similar = coll.query(
                vectors=zvec.VectorQuery(field_name="embedding", vector=vec),
                topk=1,
            )
            if similar:
                top = similar[0]
                score = top.score if hasattr(top, "score") else 0
                if score >= DEDUP_SIMILARITY_THRESHOLD:
                    return {"status": "near_duplicate", "hash": h, "similar_score": score}
        except Exception:
            pass

    doc = zvec.Doc(
        id=h,
        vectors={"embedding": vec},
        fields={
            "hash":        h,
            "text":        text,
            "source":      source,
            "agent":       "context-engine",
            "tags":        tags,
            "ts":          int(time.time()),
            "embed_model": MODEL_NAME,
        },
    )
    coll.insert(doc)
    return {"status": "added", "hash": h}
```

#### `server.py` — Update `/add` endpoint to count near-duplicates

**Current `/add` return logic:**
```python
    return {"status": "added" if added > 0 else "duplicate", "chunks": len(chunks), "added": added, "hash": last_hash}
```

**Replace with:**
```python
    status = "added" if added > 0 else "duplicate"
    return {"status": status, "chunks": len(chunks), "added": added, "hash": last_hash}
```

(This is actually the same, but ensures the return is clean. The `near_duplicate` status from `add_to_collection` is handled — it's not counted as `added`, so the existing logic is correct.)

### Verification
- Add a text chunk to a collection
- Add the same text again — returns `"status": "duplicate"` (exact SHA-1 match)
- Add a very slightly modified version (change one word) — returns `"status": "near_duplicate"` (cosine > 0.95)
- Add a completely different text — returns `"status": "added"`
- Set `CONTEXT_ENGINE_DEDUP_THRESHOLD=1.0` env var to disable near-duplicate detection (only exact match)

---

## DEPENDENCY SUMMARY

```
Task 1 (chunking)    — standalone, do first
Task 2 (model)       — standalone, changes EMBED_DIM
Task 3 (schema)      — depends on Task 2 (MODEL_NAME reference)
Task 4 (auth token)  — standalone
Task 5 (auth reads)  — depends on Task 4 (needs dynamic token loading)
Task 6 (HTML chunks) — depends on Task 1 (uses the new chunk_fn param)
Task 7 (dedup)       — depends on Task 2 and 3 (uses new embedding + model field)
```

**Execution order**: 1 → 2 → 3 → 4 → 5 → 6 → 7

## FILES MODIFIED (complete list)

| File | Tasks |
|---|---|
| `context_engine_config.py` | 1, 2, 4, 7 |
| `server.py` | 1, 2, 3, 4, 5, 7 |
| `crawler.py` | 1, 6 |
| `mcp_server.py` | 5 |
| `install.sh` | 4 |
| `extension/config.js` | 4 |
| `extension/background.js` | 4 |
| `extension/popup.js` | 4, 5 |

## TESTING AFTER ALL TASKS

1. **Server starts cleanly**: `.venv/bin/python3 server.py` logs the new model name and "ready"
2. **Health check**: `curl http://localhost:11811/health` returns 200 with `"model": "BAAI/bge-base-en-v1.5"`
3. **Auth required on reads**: `curl http://localhost:11811/collections` returns 401
4. **Auth works**: `curl http://localhost:11811/collections -H "X-Context-Token: $(cat ~/.context-engine/token)"` returns 200
5. **Add works**: POST to `/add` with auth token, verify chunks are stored
6. **Search works**: POST to `/search` with auth token, verify results include `embed_model` field
7. **Crawl works**: POST to `/crawl`, verify chunks have heading structure from HTML
8. **Dedup works**: Add similar text twice, second returns `near_duplicate`
9. **MCP works**: Run `mcp_server.py`, call `search_docs` and `list_collections` — both succeed
10. **No `from server import` in crawler.py**: `grep "from server" crawler.py` returns nothing
