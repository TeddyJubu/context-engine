#!/usr/bin/env python3
"""
Context Engine — Multi-collection semantic memory server
Port: 11811 | Data: ~/.context-engine/collections/
"""

import time
import uuid
import hashlib
import gc
import json
import logging
import re
import shutil
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, Optional

import zvec
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
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

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_K = DEFAULT_TOP_K
COLLECTION_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
MIGRATION_BATCH_SIZE = 256
MIGRATION_TMP_SUFFIX = ".tmp-migration"
MIGRATION_BAK_SUFFIX = ".bak"
OPTIONAL_SCHEMA_FIELD_NAMES = {"source_type", "metadata_json", "embed_model"}

COLL_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("context-engine")

# ── Globals ───────────────────────────────────────────────────────────────────
embedder = None
collections: dict[str, zvec.Collection] = {}
_crawl_tasks: dict[str, dict] = {}

# ── Schema ────────────────────────────────────────────────────────────────────
def make_schema(name: str) -> zvec.CollectionSchema:
    return zvec.CollectionSchema(
        name=name,
        fields=[
            zvec.FieldSchema(name="hash",        data_type=zvec.DataType.STRING, nullable=False),
            zvec.FieldSchema(name="text",        data_type=zvec.DataType.STRING, nullable=False),
            zvec.FieldSchema(name="source",      data_type=zvec.DataType.STRING, nullable=True),
            zvec.FieldSchema(name="agent",       data_type=zvec.DataType.STRING, nullable=True),
            zvec.FieldSchema(name="tags",        data_type=zvec.DataType.ARRAY_STRING, nullable=True),
            zvec.FieldSchema(name="ts",          data_type=zvec.DataType.INT64, nullable=True),
            zvec.FieldSchema(name="source_type", data_type=zvec.DataType.STRING, nullable=True),
            zvec.FieldSchema(name="metadata_json", data_type=zvec.DataType.STRING, nullable=True),
            zvec.FieldSchema(name="embed_model", data_type=zvec.DataType.STRING, nullable=True),
        ],
        vectors=[
            zvec.VectorSchema(
                name="embedding",
                data_type=zvec.DataType.VECTOR_FP32,
                dimension=EMBED_DIM,
            ),
        ],
    )

COLLECTION_OPTION = zvec.CollectionOption(read_only=False, enable_mmap=True)

# ── Pydantic models ──────────────────────────────────────────────────────────
class CreateCollectionRequest(BaseModel):
    name: str

class AddRequest(BaseModel):
    text: str
    collection: str
    source: str = "manual"
    tags: list[str] = Field(default_factory=list)
    source_type: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

class SearchRequest(BaseModel):
    query: str
    collection: Optional[str] = None
    top_k: int = DEFAULT_K
    filter_tags: list[str] = Field(default_factory=list)

class CrawlRequest(BaseModel):
    url: str
    collection: str
    max_pages: int = 200
    path_prefix: Optional[str] = None

# ── Helpers ───────────────────────────────────────────────────────────────────

def embed(text: str) -> list[float]:
    vec = embedder.encode([text], normalize_embeddings=True)[0]
    return vec.astype("float32").tolist()

def fact_hash(text: str) -> str:
    return hashlib.sha1(text.strip().encode()).hexdigest()[:20]

def chunk_text(text: str, size: int = 2048, overlap: int = 200) -> list[str]:
    """Recursive character splitter. Tries separators in order of preference:
    double-newline > single-newline > space > hard cut.
    Size and overlap are in characters (~4 chars per token).
    Default: ~512 tokens chunk, ~50 tokens overlap.
    """
    safe_size = max(size, 1)
    safe_overlap = max(0, min(overlap, safe_size - 1))
    separators = ["\n\n", "\n", " "]

    def _split(text: str, seps: list[str]) -> list[str]:
        if len(text) <= safe_size:
            return [text] if text.strip() else []

        sep = ""
        for s in seps:
            if s in text:
                sep = s
                break

        if not sep:
            chunks = []
            step = max(1, safe_size - safe_overlap)
            for i in range(0, len(text), step):
                chunk = text[i:i + safe_size].strip()
                if chunk:
                    chunks.append(chunk)
            return chunks

        parts = text.split(sep)
        remaining_seps = seps[seps.index(sep) + 1:] if sep in seps else []

        chunks = []
        current = ""
        for part in parts:
            candidate = (current + sep + part).strip() if current else part.strip()
            if len(candidate) <= safe_size:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                if len(part) > safe_size:
                    chunks.extend(_split(part.strip(), remaining_seps))
                    current = ""
                else:
                    current = part.strip()
        if current:
            chunks.append(current)

        return chunks

    raw_chunks = _split(text, separators)

    if not raw_chunks:
        return [text[:safe_size]] if text.strip() else []

    if safe_overlap <= 0 or len(raw_chunks) <= 1:
        return raw_chunks

    result = [raw_chunks[0]]
    for i in range(1, len(raw_chunks)):
        prev = raw_chunks[i - 1]
        overlap_text = prev[-safe_overlap:] if len(prev) > safe_overlap else prev
        space_idx = overlap_text.find(" ")
        if space_idx > 0:
            overlap_text = overlap_text[space_idx + 1:]
        combined = (overlap_text + " " + raw_chunks[i]).strip()
        if len(combined) > safe_size:
            combined = combined[-safe_size:]
        result.append(combined)

    return result

def normalize_collection_name(name: str) -> str:
    return name.strip().lower().replace(" ", "-")

def validate_collection_name(name: str) -> str:
    normalized = normalize_collection_name(name)
    if not normalized:
        raise HTTPException(400, "Collection name cannot be empty")
    if not COLLECTION_NAME_RE.fullmatch(normalized):
        raise HTTPException(400, "Invalid collection name. Use lowercase letters, numbers, and hyphens.")
    return normalized

def resolve_existing_collection_name(name: str) -> Optional[str]:
    raw_name = name.strip()
    normalized = normalize_collection_name(name)
    candidates = []
    if raw_name:
        candidates.append(raw_name)
    if normalized and normalized not in candidates:
        candidates.append(normalized)

    for candidate in candidates:
        if candidate in collections or (COLL_DIR / candidate).exists():
            return candidate
    return None

def require_write_token(
    x_context_token: Optional[str] = Header(default=None, alias=AUTH_HEADER),
) -> None:
    if x_context_token != AUTH_TOKEN:
        raise HTTPException(401, "Unauthorized")

def is_visible_collection_dir(path: Path) -> bool:
    return (
        path.is_dir()
        and not path.name.endswith(MIGRATION_BAK_SUFFIX)
        and MIGRATION_TMP_SUFFIX not in path.name
    )

def collection_field_names(coll: zvec.Collection) -> set[str]:
    return {field.name for field in coll.schema.fields}

def collection_embedding_dimension(coll: zvec.Collection) -> Optional[int]:
    for vector in coll.schema.vectors:
        if vector.name == "embedding":
            return vector.dimension
    return None

def collection_needs_schema_upgrade(coll: zvec.Collection) -> bool:
    return (
        not OPTIONAL_SCHEMA_FIELD_NAMES.issubset(collection_field_names(coll))
        or collection_embedding_dimension(coll) != EMBED_DIM
    )

def iter_batches(items: list[str], batch_size: int):
    for index in range(0, len(items), batch_size):
        yield items[index:index + batch_size]

def serialize_metadata(metadata: Optional[dict[str, Any]]) -> Optional[str]:
    if not metadata:
        return None
    return json.dumps(metadata, sort_keys=True)

def parse_metadata_json(value: Optional[str]) -> Optional[dict[str, Any]]:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None

def rebuild_collection_doc(existing_doc: zvec.Doc) -> zvec.Doc:
    fields = dict(existing_doc.fields or {})
    vectors = dict(existing_doc.vectors or {})
    stored_embedding = vectors.get("embedding")
    try:
        embedding = list(stored_embedding) if stored_embedding is not None else None
    except TypeError:
        embedding = None
    reembedded = embedding is None or len(embedding) != EMBED_DIM
    if reembedded:
        embedding = embed(fields.get("text", ""))
    return zvec.Doc(
        id=str(existing_doc.id),
        vectors={"embedding": embedding},
        fields={
            "hash": fields.get("hash", str(existing_doc.id)),
            "text": fields.get("text", ""),
            "source": fields.get("source"),
            "agent": fields.get("agent"),
            "tags": fields.get("tags"),
            "ts": fields.get("ts"),
            "source_type": fields.get("source_type"),
            "metadata_json": fields.get("metadata_json"),
            "embed_model": MODEL_NAME if reembedded else fields.get("embed_model"),
        },
    )

def migrate_collection_schema(name: str, coll_path: Path, coll: Optional[zvec.Collection] = None) -> zvec.Collection:
    log.info("Migrating collection schema for %s", name)
    existing = coll or zvec.open(str(coll_path), option=COLLECTION_OPTION)
    doc_count = max(coll_doc_count(existing), 0)
    query_topk = doc_count if doc_count > 0 else 1
    existing_ids = [str(doc.id) for doc in existing.query(topk=query_topk)]

    tmp_path = coll_path.with_name(coll_path.name + MIGRATION_TMP_SUFFIX)
    bak_path = coll_path.with_name(coll_path.name + MIGRATION_BAK_SUFFIX)

    if tmp_path.exists():
        shutil.rmtree(tmp_path)
    if bak_path.exists():
        shutil.rmtree(bak_path)

    migrated = zvec.create_and_open(str(tmp_path), schema=make_schema(name), option=COLLECTION_OPTION)
    for batch_ids in iter_batches(existing_ids, MIGRATION_BATCH_SIZE):
        fetched = existing.fetch(ids=batch_ids)
        for doc_id in batch_ids:
            if doc_id not in fetched:
                continue
            migrated.insert(rebuild_collection_doc(fetched[doc_id]))

    try:
        migrated.optimize()
    except Exception:
        pass

    del migrated
    gc.collect()

    try:
        existing.optimize()
    except Exception:
        pass
    del existing
    gc.collect()

    coll_path.rename(bak_path)
    tmp_path.rename(coll_path)

    try:
        reopened = zvec.open(str(coll_path), option=COLLECTION_OPTION)
    except Exception:
        if coll_path.exists():
            shutil.rmtree(coll_path)
        bak_path.rename(coll_path)
        raise

    shutil.rmtree(bak_path, ignore_errors=True)
    return reopened

def open_collection_with_schema(name: str) -> zvec.Collection:
    coll_path = COLL_DIR / name
    coll = zvec.open(str(coll_path), option=COLLECTION_OPTION)
    if collection_needs_schema_upgrade(coll):
        try:
            coll.optimize()
        except Exception:
            pass
        del coll
        gc.collect()
        coll = migrate_collection_schema(name, coll_path)
    return coll

def get_collection(name: str) -> zvec.Collection:
    """Lazy-open a collection by name."""
    resolved_name = resolve_existing_collection_name(name)
    name = resolved_name or validate_collection_name(name)
    if name in collections:
        return collections[name]
    coll_path = str(COLL_DIR / name)
    if Path(coll_path).exists():
        coll = open_collection_with_schema(name)
        collections[name] = coll
        return coll
    raise HTTPException(404, f"Collection '{name}' not found")

def ensure_collection(name: str) -> zvec.Collection:
    """Get or create a collection."""
    resolved_name = resolve_existing_collection_name(name)
    name = resolved_name or validate_collection_name(name)
    if name in collections:
        return collections[name]
    coll_path = str(COLL_DIR / name)
    if Path(coll_path).exists():
        coll = open_collection_with_schema(name)
    else:
        coll = zvec.create_and_open(coll_path, schema=make_schema(name), option=COLLECTION_OPTION)
    collections[name] = coll
    return coll

def coll_doc_count(coll: zvec.Collection) -> int:
    try:
        return coll.stats.doc_count
    except Exception:
        return -1

def add_to_collection(
    text: str,
    coll: zvec.Collection,
    source: str = "manual",
    tags: Optional[list[str]] = None,
    source_type: Optional[str] = None,
    metadata_json: Optional[str] = None,
) -> dict:
    h = fact_hash(text)
    resolved_tags = tags or []
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
            "tags":        resolved_tags,
            "ts":          int(time.time()),
            "source_type": source_type,
            "metadata_json": metadata_json,
            "embed_model": MODEL_NAME,
        },
    )
    coll.insert(doc)
    return {"status": "added", "hash": h}

# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global embedder

    log.info("Loading embedding model: %s", MODEL_NAME)
    from sentence_transformers import SentenceTransformer
    embedder = SentenceTransformer(MODEL_NAME)
    log.info("Embedding model loaded.")

    # Verify model output dimension matches config
    test_vec = embedder.encode(["test"], normalize_embeddings=True)[0]
    actual_dim = len(test_vec)
    if actual_dim != EMBED_DIM:
        log.error(
            "EMBED_DIM mismatch: config says %d but model '%s' produces %d-dim vectors. "
            "Set CONTEXT_ENGINE_EMBED_DIM=%d or change EMBED_MODEL. Exiting.",
            EMBED_DIM, MODEL_NAME, actual_dim, actual_dim,
        )
        raise SystemExit(1)

    # Pre-open existing collections
    for d in COLL_DIR.iterdir():
        if is_visible_collection_dir(d):
            try:
                collections[d.name] = open_collection_with_schema(d.name)
                log.info("Opened collection: %s (%d docs)", d.name, coll_doc_count(collections[d.name]))
            except Exception as e:
                log.warning("Failed to open collection %s: %s", d.name, e)

    log.info("Context Engine ready. %d collections loaded.", len(collections))
    yield
    log.info("Shutting down — optimizing collections...")
    for name, coll in collections.items():
        try:
            coll.optimize()
        except Exception:
            pass

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Context Engine", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_origin_regex=CORS_ALLOW_ORIGIN_REGEX,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", AUTH_HEADER],
)

@app.get("/health")
def health():
    return {
        "status": "ok",
        "collections_count": len(collections),
        "model": MODEL_NAME,
    }

@app.post("/token-check")
def token_check(_auth: None = Depends(require_write_token)):
    return {"status": "valid"}

@app.get("/collections")
def list_collections(_auth: None = Depends(require_write_token)):
    result = []
    for name, coll in collections.items():
        result.append({"name": name, "doc_count": coll_doc_count(coll)})
    # Also scan disk for collections not yet opened
    for d in COLL_DIR.iterdir():
        if is_visible_collection_dir(d) and d.name not in collections:
            result.append({"name": d.name, "doc_count": -1})
    return result

@app.post("/collections")
def create_collection(req: CreateCollectionRequest, _auth: None = Depends(require_write_token)):
    name = validate_collection_name(req.name)
    ensure_collection(name)
    return {"name": name, "status": "created"}

@app.delete("/collections/{name}")
def delete_collection(name: str, _auth: None = Depends(require_write_token)):
    name = resolve_existing_collection_name(name) or validate_collection_name(name)
    if name in collections:
        try:
            collections[name].optimize()
        except Exception:
            pass
        del collections[name]
    coll_path = COLL_DIR / name
    if coll_path.exists():
        shutil.rmtree(coll_path)
        return {"status": "deleted"}
    raise HTTPException(404, f"Collection '{name}' not found")

@app.post("/add")
def add_fact(req: AddRequest, _auth: None = Depends(require_write_token)):
    coll = ensure_collection(req.collection)
    chunks = chunk_text(req.text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
    metadata_json = serialize_metadata(req.metadata)
    added = 0
    saw_near_duplicate = False
    near_duplicate_info = None
    last_hash = ""
    for chunk in chunks:
        result = add_to_collection(
            chunk,
            coll,
            source=req.source,
            tags=req.tags,
            source_type=req.source_type,
            metadata_json=metadata_json,
        )
        if result["status"] == "added":
            added += 1
        elif result["status"] == "near_duplicate":
            saw_near_duplicate = True
            near_duplicate_info = {"similar_score": result.get("similar_score")}
        last_hash = result["hash"]
    status = "added" if added > 0 else "near_duplicate" if saw_near_duplicate else "duplicate"
    response = {"status": status, "chunks": len(chunks), "added": added, "hash": last_hash}
    if near_duplicate_info and near_duplicate_info.get("similar_score") is not None:
        response["near_duplicate"] = near_duplicate_info
    return response

@app.post("/search")
def search(req: SearchRequest, _auth: None = Depends(require_write_token)):
    vec = embed(req.query)
    all_results = []

    target_collections = {}
    if req.collection:
        coll = get_collection(req.collection)
        cname = resolve_existing_collection_name(req.collection) or validate_collection_name(req.collection)
        target_collections[cname] = coll
    else:
        target_collections = dict(collections)

    for cname, coll in target_collections.items():
        if coll_doc_count(coll) == 0:
            continue
        try:
            results = coll.query(
                vectors=zvec.VectorQuery(field_name="embedding", vector=vec),
                topk=req.top_k,
            )
        except Exception:
            continue

        for r in results:
            item = {
                "text":        r.fields.get("text", ""),
                "source":      r.fields.get("source", ""),
                "tags":        r.fields.get("tags", []),
                "score":       r.score if hasattr(r, "score") else None,
                "collection":  cname,
                "source_type": r.fields.get("source_type"),
                "metadata":    parse_metadata_json(r.fields.get("metadata_json")),
                "embed_model": r.fields.get("embed_model", ""),
            }
            if req.filter_tags:
                if not any(t in (item["tags"] or []) for t in req.filter_tags):
                    continue
            all_results.append(item)

    # Sort by score descending, take top_k
    all_results.sort(key=lambda x: x.get("score") or 0, reverse=True)
    return {"results": all_results[:req.top_k], "query": req.query}

@app.post("/crawl")
async def start_crawl(req: CrawlRequest, _auth: None = Depends(require_write_token)):
    import asyncio
    from crawler import crawl_site

    coll = ensure_collection(req.collection)
    task_id = str(uuid.uuid4())[:8]
    _crawl_tasks[task_id] = {"status": "running", "pages_crawled": 0, "pages_total": req.max_pages}

    async def run_crawl():
        try:
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
            _crawl_tasks[task_id]["status"] = "done"
        except Exception as e:
            _crawl_tasks[task_id]["status"] = f"error: {e}"
            log.error("Crawl %s failed: %s", task_id, e)

    asyncio.create_task(run_crawl())
    return {"task_id": task_id, "status": "started"}

@app.get("/crawl/{task_id}")
def crawl_status(task_id: str, _auth: None = Depends(require_write_token)):
    if task_id not in _crawl_tasks:
        raise HTTPException(404, "Unknown crawl task")
    return _crawl_tasks[task_id]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=SERVER_HOST, port=SERVER_PORT, reload=False)
