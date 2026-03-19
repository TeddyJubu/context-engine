#!/usr/bin/env python3
"""
Context Engine — Multi-collection semantic memory server
Port: 11811 | Data: ~/.context-engine/collections/
"""

import json
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
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import sys

def get_model_cache_dir() -> Optional[str]:
    """Return bundled model cache path when running inside a PyInstaller .app, else None."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "sentence_transformers_cache")
    return None

# ── Config ────────────────────────────────────────────────────────────────────
DATA_DIR    = Path(os.environ.get("CONTEXT_ENGINE_DIR", Path.home() / ".context-engine"))
COLL_DIR    = DATA_DIR / "collections"
CRAWL_DIR   = DATA_DIR / "crawls"
MODEL_NAME  = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")
EMBED_DIM   = 384
DEFAULT_K   = int(os.environ.get("CONTEXT_TOP_K", "8"))

COLL_DIR.mkdir(parents=True, exist_ok=True)
CRAWL_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("context-engine")

# ── Globals ───────────────────────────────────────────────────────────────────
embedder = None
collections: dict[str, zvec.Collection] = {}
_crawl_tasks: dict[str, dict] = {}

# BM25 indexes — keyed by collection name
_bm25_corpus: dict[str, list[dict]] = {}   # name -> [{hash, text, source, tags}]
_bm25_index: dict = {}                      # name -> BM25Okapi instance

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
    use_bm25: bool = True  # hybrid semantic + keyword search

class CrawlRequest(BaseModel):
    url: str
    collection: str
    max_pages: int = 200
    path_prefix: Optional[str] = None
    force_restart: bool = False  # if True, discard existing checkpoint

class RestoreRequest(BaseModel):
    collection: str
    documents: list[dict]  # list of {text, source?, tags?}

# ── BM25 helpers ─────────────────────────────────────────────────────────────

def _sidecar_path(coll_name: str) -> Path:
    return COLL_DIR / coll_name / "texts.jsonl"

def _rebuild_bm25(coll_name: str) -> None:
    """Rebuild BM25 index from in-memory corpus for a collection."""
    corpus = _bm25_corpus.get(coll_name, [])
    if not corpus:
        _bm25_index.pop(coll_name, None)
        return
    try:
        from rank_bm25 import BM25Okapi
        tokenized = [doc["text"].lower().split() for doc in corpus]
        _bm25_index[coll_name] = BM25Okapi(tokenized)
    except ImportError:
        pass  # rank_bm25 not installed; keyword search unavailable

def _load_bm25_index(coll_name: str) -> None:
    """Load texts.jsonl sidecar and build BM25 index for a collection."""
    sidecar = _sidecar_path(coll_name)
    docs = []
    if sidecar.exists():
        with open(sidecar) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        docs.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    _bm25_corpus[coll_name] = docs
    if docs:
        _rebuild_bm25(coll_name)

def _append_sidecar(coll_name: str, h: str, text: str, source: str, tags: list) -> None:
    """Append a new document to the sidecar and update the BM25 index."""
    doc = {"hash": h, "text": text, "source": source, "tags": tags}
    sidecar = _sidecar_path(coll_name)
    with open(sidecar, "a") as f:
        f.write(json.dumps(doc) + "\n")
    if coll_name not in _bm25_corpus:
        _bm25_corpus[coll_name] = []
    _bm25_corpus[coll_name].append(doc)
    _rebuild_bm25(coll_name)

def _bm25_search(coll_name: str, query: str, top_k: int) -> list[dict]:
    """Return BM25-ranked results for a query within a collection."""
    if coll_name not in _bm25_index:
        return []
    corpus = _bm25_corpus.get(coll_name, [])
    if not corpus:
        return []
    scores = _bm25_index[coll_name].get_scores(query.lower().split())
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    results = []
    for idx in ranked[:top_k]:
        if scores[idx] <= 0:
            break
        doc = corpus[idx]
        results.append({
            "text":       doc["text"],
            "source":     doc.get("source", ""),
            "tags":       doc.get("tags", []),
            "bm25_score": float(scores[idx]),
            "collection": coll_name,
        })
    return results

def _rrf_merge(semantic_lists: list[list[dict]], bm25_lists: list[list[dict]], k: int = 60) -> list[dict]:
    """Merge semantic and BM25 result lists using Reciprocal Rank Fusion."""
    scores: dict[str, float] = {}
    doc_by_key: dict[str, dict] = {}

    def add_ranked(ranked: list[dict], weight: float = 1.0):
        for rank, doc in enumerate(ranked):
            key = doc.get("text", "")[:80]  # deduplicate by text prefix
            if key not in scores:
                scores[key] = 0.0
                doc_by_key[key] = doc
            scores[key] += weight / (k + rank + 1)

    for lst in semantic_lists:
        add_ranked(lst, weight=1.0)
    for lst in bm25_lists:
        add_ranked(lst, weight=0.75)  # slightly down-weight keyword results

    sorted_keys = sorted(scores, key=lambda x: scores[x], reverse=True)
    merged = []
    for key in sorted_keys:
        doc = dict(doc_by_key[key])
        doc["score"] = round(scores[key], 6)
        merged.append(doc)
    return merged

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
        _load_bm25_index(name)
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
    _load_bm25_index(name)
    return coll

def coll_doc_count(coll: zvec.Collection) -> int:
    try:
        return coll.stats.doc_count
    except Exception:
        return -1

def add_to_collection(text: str, coll_name: str, source: str = "manual", tags: list[str] = []) -> dict:
    """Add a text chunk to a named collection, maintaining the BM25 sidecar."""
    coll = ensure_collection(coll_name)
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
    _append_sidecar(coll_name, h, text, source, tags)
    return {"status": "added", "hash": h}

def _crawl_task_id(url: str, collection: str) -> str:
    """Deterministic task ID based on URL + collection for checkpoint continuity."""
    key = f"{url.rstrip('/')}:{collection}"
    return hashlib.md5(key.encode()).hexdigest()[:8]

# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global embedder

    log.info("Loading embedding model: %s", MODEL_NAME)
    from sentence_transformers import SentenceTransformer
    embedder = SentenceTransformer(MODEL_NAME, cache_folder=get_model_cache_dir())
    log.info("Embedding model loaded.")

    # Pre-open existing collections and build BM25 indexes
    for d in COLL_DIR.iterdir():
        if d.is_dir():
            try:
                coll = zvec.open(str(d), option=COLLECTION_OPTION)
                collections[d.name] = coll
                _load_bm25_index(d.name)
                log.info(
                    "Opened collection: %s (%d docs, %d bm25 docs)",
                    d.name,
                    coll_doc_count(coll),
                    len(_bm25_corpus.get(d.name, [])),
                )
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
    _bm25_corpus.pop(name, None)
    _bm25_index.pop(name, None)
    coll_path = COLL_DIR / name
    if coll_path.exists():
        shutil.rmtree(coll_path)
        return {"status": "deleted"}
    raise HTTPException(404, f"Collection '{name}' not found")

@app.post("/add")
def add_fact(req: AddRequest):
    chunks = chunk_text(req.text)
    added = 0
    last_hash = ""
    for chunk in chunks:
        result = add_to_collection(chunk, req.collection, source=req.source, tags=req.tags)
        if result["status"] == "added":
            added += 1
        last_hash = result["hash"]
    return {"status": "added" if added > 0 else "duplicate", "chunks": len(chunks), "added": added, "hash": last_hash}

@app.post("/search")
def search(req: SearchRequest):
    vec = embed(req.query)

    target_collections: dict[str, zvec.Collection] = {}
    if req.collection:
        target_collections[req.collection] = get_collection(req.collection)
    else:
        target_collections = dict(collections)

    semantic_by_coll: list[list[dict]] = []
    bm25_by_coll: list[list[dict]] = []

    for cname, coll in target_collections.items():
        if coll_doc_count(coll) == 0:
            continue

        # Semantic search
        sem_results = []
        try:
            results = coll.query(
                vectors=zvec.VectorQuery(field_name="embedding", vector=vec),
                topk=req.top_k,
            )
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
                sem_results.append(item)
        except Exception:
            pass
        semantic_by_coll.append(sem_results)

        # BM25 keyword search
        if req.use_bm25:
            bm25_results = _bm25_search(cname, req.query, req.top_k)
            if req.filter_tags:
                bm25_results = [
                    r for r in bm25_results
                    if any(t in (r.get("tags") or []) for t in req.filter_tags)
                ]
            bm25_by_coll.append(bm25_results)

    # Merge via RRF if BM25 enabled, otherwise just sort semantic results
    if req.use_bm25:
        all_results = _rrf_merge(semantic_by_coll, bm25_by_coll)
    else:
        all_results = [item for lst in semantic_by_coll for item in lst]
        all_results.sort(key=lambda x: x.get("score") or 0, reverse=True)

    return {"results": all_results[:req.top_k], "query": req.query}

@app.post("/crawl")
async def start_crawl(req: CrawlRequest):
    import asyncio
    from crawler import crawl_site

    task_id = _crawl_task_id(req.url, req.collection)
    checkpoint_path = CRAWL_DIR / f"{task_id}.json"

    # Prevent double-starting a running crawl
    if task_id in _crawl_tasks and _crawl_tasks[task_id].get("status") == "running":
        return {"task_id": task_id, "status": "already_running"}

    if req.force_restart and checkpoint_path.exists():
        checkpoint_path.unlink()
        log.info("Checkpoint cleared for task %s (force_restart)", task_id)

    resuming = checkpoint_path.exists()
    ensure_collection(req.collection)
    _crawl_tasks[task_id] = {
        "status": "running",
        "pages_crawled": 0,
        "pages_total": req.max_pages,
        "resuming": resuming,
    }

    async def run_crawl():
        try:
            await crawl_site(
                url=req.url,
                coll_name=req.collection,
                task_state=_crawl_tasks[task_id],
                max_pages=req.max_pages,
                path_prefix=req.path_prefix,
                add_fn=add_to_collection,
                checkpoint_path=checkpoint_path,
            )
            _crawl_tasks[task_id]["status"] = "done"
        except Exception as e:
            _crawl_tasks[task_id]["status"] = f"error: {e}"
            log.error("Crawl %s failed: %s", task_id, e)

    asyncio.create_task(run_crawl())
    return {"task_id": task_id, "status": "started", "resuming": resuming}

@app.get("/crawl/{task_id}")
def crawl_status(task_id: str):
    if task_id not in _crawl_tasks:
        raise HTTPException(404, "Unknown crawl task")
    return _crawl_tasks[task_id]

# ── Backup / Restore ──────────────────────────────────────────────────────────

@app.get("/backup/{name}")
def backup_collection(name: str):
    """Export all documents in a collection as a portable JSON backup."""
    # Ensure collection exists
    get_collection(name)
    docs = _bm25_corpus.get(name, [])
    if not docs:
        # Try loading from sidecar in case index wasn't loaded
        _load_bm25_index(name)
        docs = _bm25_corpus.get(name, [])
    return {
        "collection": name,
        "doc_count": len(docs),
        "documents": docs,
        "exported_at": int(time.time()),
    }

@app.post("/restore")
def restore_collection(req: RestoreRequest):
    """Re-embed and restore documents from a backup into a collection."""
    if not req.documents:
        raise HTTPException(400, "No documents provided")

    added = 0
    skipped = 0
    for doc in req.documents:
        text = doc.get("text", "").strip()
        if not text:
            skipped += 1
            continue
        result = add_to_collection(
            text,
            req.collection,
            source=doc.get("source", "restored"),
            tags=doc.get("tags", []),
        )
        if result["status"] == "added":
            added += 1
        else:
            skipped += 1

    return {
        "status": "restored",
        "collection": req.collection,
        "added": added,
        "skipped": skipped,
        "total": len(req.documents),
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=11811, reload=False)
