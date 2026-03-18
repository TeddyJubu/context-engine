#!/usr/bin/env python3
"""
Context Engine — Multi-collection semantic memory server
Port: 11811 | Data: ~/.context-engine/collections/
"""

import os
import time
import uuid
import hashlib
import logging
import shutil
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

import zvec
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────────────────────
DATA_DIR    = Path(os.environ.get("CONTEXT_ENGINE_DIR", Path.home() / ".context-engine"))
COLL_DIR    = DATA_DIR / "collections"
MODEL_NAME  = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")
EMBED_DIM   = 384
DEFAULT_K   = int(os.environ.get("CONTEXT_TOP_K", "8"))

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
            zvec.FieldSchema(name="hash",   data_type=zvec.DataType.STRING, nullable=False),
            zvec.FieldSchema(name="text",   data_type=zvec.DataType.STRING, nullable=False),
            zvec.FieldSchema(name="source", data_type=zvec.DataType.STRING, nullable=True),
            zvec.FieldSchema(name="agent",  data_type=zvec.DataType.STRING, nullable=True),
            zvec.FieldSchema(name="tags",   data_type=zvec.DataType.ARRAY_STRING, nullable=True),
            zvec.FieldSchema(name="ts",     data_type=zvec.DataType.INT64, nullable=True),
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
    tags: list[str] = []

class SearchRequest(BaseModel):
    query: str
    collection: Optional[str] = None
    top_k: int = DEFAULT_K
    filter_tags: list[str] = []

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

def get_collection(name: str) -> zvec.Collection:
    """Lazy-open a collection by name."""
    if name in collections:
        return collections[name]
    coll_path = str(COLL_DIR / name)
    if Path(coll_path).exists():
        coll = zvec.open(coll_path, option=COLLECTION_OPTION)
        collections[name] = coll
        return coll
    raise HTTPException(404, f"Collection '{name}' not found")

def ensure_collection(name: str) -> zvec.Collection:
    """Get or create a collection."""
    if name in collections:
        return collections[name]
    coll_path = str(COLL_DIR / name)
    if Path(coll_path).exists():
        coll = zvec.open(coll_path, option=COLLECTION_OPTION)
    else:
        coll = zvec.create_and_open(coll_path, schema=make_schema(name), option=COLLECTION_OPTION)
    collections[name] = coll
    return coll

def coll_doc_count(coll: zvec.Collection) -> int:
    try:
        return coll.stats.doc_count
    except Exception:
        return -1

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
            "hash":   h,
            "text":   text,
            "source": source,
            "agent":  "context-engine",
            "tags":   tags,
            "ts":     int(time.time()),
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

    # Pre-open existing collections
    for d in COLL_DIR.iterdir():
        if d.is_dir():
            try:
                collections[d.name] = zvec.open(str(d), option=COLLECTION_OPTION)
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
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {
        "status": "ok",
        "collections_count": len(collections),
        "model": MODEL_NAME,
    }

@app.get("/collections")
def list_collections():
    result = []
    for name, coll in collections.items():
        result.append({"name": name, "doc_count": coll_doc_count(coll)})
    # Also scan disk for collections not yet opened
    for d in COLL_DIR.iterdir():
        if d.is_dir() and d.name not in collections:
            result.append({"name": d.name, "doc_count": -1})
    return result

@app.post("/collections")
def create_collection(req: CreateCollectionRequest):
    name = req.name.strip().lower().replace(" ", "-")
    if not name:
        raise HTTPException(400, "Collection name cannot be empty")
    ensure_collection(name)
    return {"name": name, "status": "created"}

@app.delete("/collections/{name}")
def delete_collection(name: str):
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
def add_fact(req: AddRequest):
    coll = ensure_collection(req.collection)
    chunks = chunk_text(req.text)
    added = 0
    last_hash = ""
    for chunk in chunks:
        result = add_to_collection(chunk, coll, source=req.source, tags=req.tags)
        if result["status"] == "added":
            added += 1
        last_hash = result["hash"]
    return {"status": "added" if added > 0 else "duplicate", "chunks": len(chunks), "added": added, "hash": last_hash}

@app.post("/search")
def search(req: SearchRequest):
    vec = embed(req.query)
    all_results = []

    target_collections = {}
    if req.collection:
        target_collections[req.collection] = get_collection(req.collection)
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
                "text":       r.fields.get("text", ""),
                "source":     r.fields.get("source", ""),
                "tags":       r.fields.get("tags", []),
                "score":      r.score if hasattr(r, "score") else None,
                "collection": cname,
            }
            if req.filter_tags:
                if not any(t in (item["tags"] or []) for t in req.filter_tags):
                    continue
            all_results.append(item)

    # Sort by score descending, take top_k
    all_results.sort(key=lambda x: x.get("score") or 0, reverse=True)
    return {"results": all_results[:req.top_k], "query": req.query}

@app.post("/crawl")
async def start_crawl(req: CrawlRequest):
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
            )
            _crawl_tasks[task_id]["status"] = "done"
        except Exception as e:
            _crawl_tasks[task_id]["status"] = f"error: {e}"
            log.error("Crawl %s failed: %s", task_id, e)

    asyncio.create_task(run_crawl())
    return {"task_id": task_id, "status": "started"}

@app.get("/crawl/{task_id}")
def crawl_status(task_id: str):
    if task_id not in _crawl_tasks:
        raise HTTPException(404, "Unknown crawl task")
    return _crawl_tasks[task_id]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=11811, reload=False)
