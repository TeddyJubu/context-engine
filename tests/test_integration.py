"""
Integration tests for Context Engine.

Tests the full stack: HTTP API → server logic → zvec storage → BM25 index.
The SentenceTransformer model is mocked with a deterministic fake that produces
normalized 384-dim vectors seeded from the input text hash.
"""

import json
import time
import pytest
import numpy as np
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient


# ── Fake embedder ─────────────────────────────────────────────────────────────

def _make_fake_embedder():
    """Deterministic 384-dim embedder — same text always gets the same vector."""
    mock = MagicMock()

    def encode(texts, normalize_embeddings=True):
        result = []
        for text in texts:
            seed = abs(hash(text)) % (2 ** 31)
            rng = np.random.RandomState(seed)
            vec = rng.randn(384).astype("float32")
            if normalize_embeddings:
                norm = np.linalg.norm(vec)
                vec = vec / (norm + 1e-8)
            result.append(vec)
        return result

    mock.encode = encode
    return mock


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path):
    """
    Provide a TestClient with:
    - Isolated data directories (tmp_path)
    - Fake embedding model (no network/GPU needed)
    - Clean in-memory state per test
    """
    import server

    coll_dir = tmp_path / "collections"
    coll_dir.mkdir()
    crawl_dir = tmp_path / "crawls"
    crawl_dir.mkdir()

    fake_embedder = _make_fake_embedder()

    # sentence_transformers is already stubbed in conftest.py; override its
    # return value so the lifespan assigns our fake_embedder to server.embedder.
    import sentence_transformers as _st
    orig_cls = _st.SentenceTransformer
    _st.SentenceTransformer = lambda *a, **kw: fake_embedder

    with (
        patch.object(server, "COLL_DIR", coll_dir),
        patch.object(server, "CRAWL_DIR", crawl_dir),
        patch.object(server, "collections", {}),
        patch.object(server, "_bm25_corpus", {}),
        patch.object(server, "_bm25_index", {}),
        patch.object(server, "_crawl_tasks", {}),
    ):
        with TestClient(server.app, raise_server_exceptions=True) as c:
            yield c

    _st.SentenceTransformer = orig_cls


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "collections_count" in body
        assert "model" in body


# ── Collections ───────────────────────────────────────────────────────────────

class TestCollections:
    def test_create_collection(self, client):
        r = client.post("/collections", json={"name": "my-docs"})
        assert r.status_code == 200
        assert r.json()["name"] == "my-docs"

    def test_create_normalises_name(self, client):
        r = client.post("/collections", json={"name": "My Docs"})
        assert r.status_code == 200
        assert r.json()["name"] == "my-docs"

    def test_list_includes_created(self, client):
        client.post("/collections", json={"name": "list-test"})
        r = client.get("/collections")
        assert r.status_code == 200
        names = [c["name"] for c in r.json()]
        assert "list-test" in names

    def test_delete_collection(self, client):
        client.post("/collections", json={"name": "to-delete"})
        r = client.delete("/collections/to-delete")
        assert r.status_code == 200
        assert r.json()["status"] == "deleted"

    def test_delete_missing_collection_returns_404(self, client):
        r = client.delete("/collections/nonexistent")
        assert r.status_code == 404

    def test_empty_name_returns_400(self, client):
        r = client.post("/collections", json={"name": "   "})
        assert r.status_code == 400


# ── Add & semantic search ─────────────────────────────────────────────────────

class TestAddAndSearch:
    def test_add_document_returns_added(self, client):
        r = client.post("/add", json={
            "text": "Python is a general-purpose programming language.",
            "collection": "code",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "added"
        assert body["added"] >= 1

    def test_duplicate_document_returns_duplicate(self, client):
        payload = {"text": "Exact duplicate text for dedup test.", "collection": "dedup"}
        r1 = client.post("/add", json=payload)
        r2 = client.post("/add", json=payload)
        assert r1.json()["status"] == "added"
        assert r2.json()["status"] == "duplicate"

    def test_search_returns_results_after_add(self, client):
        client.post("/add", json={
            "text": "FastAPI is a modern web framework for Python.",
            "collection": "web",
        })
        r = client.post("/search", json={"query": "FastAPI web framework", "collection": "web"})
        assert r.status_code == 200
        body = r.json()
        assert "results" in body
        assert len(body["results"]) > 0

    def test_search_result_contains_expected_fields(self, client):
        client.post("/add", json={
            "text": "Docker containers package applications.",
            "collection": "infra",
            "source": "https://docker.com",
        })
        r = client.post("/search", json={"query": "containers", "collection": "infra"})
        result = r.json()["results"][0]
        assert "text" in result
        assert "source" in result
        assert "collection" in result

    def test_search_across_all_collections(self, client):
        client.post("/add", json={"text": "Kubernetes orchestrates containers.", "collection": "k8s"})
        client.post("/add", json={"text": "Terraform provisions infrastructure.", "collection": "iac"})
        r = client.post("/search", json={"query": "infrastructure"})
        assert r.status_code == 200
        assert len(r.json()["results"]) > 0

    def test_search_respects_top_k(self, client):
        for i in range(5):
            client.post("/add", json={"text": f"Document number {i} about cloud computing.", "collection": "cloud"})
        r = client.post("/search", json={"query": "cloud computing", "collection": "cloud", "top_k": 2})
        assert len(r.json()["results"]) <= 2

    def test_search_missing_collection_returns_404(self, client):
        r = client.post("/search", json={"query": "anything", "collection": "missing"})
        assert r.status_code == 404


# ── BM25 keyword search ───────────────────────────────────────────────────────

class TestBM25Search:
    def test_bm25_search_enabled_by_default(self, client):
        client.post("/add", json={
            "text": "zstd is a fast lossless compression algorithm developed by Facebook.",
            "collection": "algos",
        })
        r = client.post("/search", json={"query": "zstd compression", "collection": "algos"})
        assert r.status_code == 200
        assert len(r.json()["results"]) > 0

    def test_bm25_disabled_still_returns_semantic_results(self, client):
        client.post("/add", json={
            "text": "Redis is an in-memory key-value store.",
            "collection": "db",
        })
        r = client.post("/search", json={
            "query": "in-memory database",
            "collection": "db",
            "use_bm25": False,
        })
        assert r.status_code == 200
        assert len(r.json()["results"]) > 0

    def test_hybrid_search_includes_keyword_matches(self, client):
        client.post("/add", json={
            "text": "cryptographic hash functions: SHA-256 produces a 256-bit digest.",
            "collection": "crypto",
        })
        # Exact keyword match should surface even if semantic score varies
        r = client.post("/search", json={
            "query": "SHA-256 digest",
            "collection": "crypto",
            "use_bm25": True,
        })
        assert r.status_code == 200
        results = r.json()["results"]
        assert any("SHA-256" in res["text"] for res in results)

    def test_results_have_score_field_when_merged(self, client):
        client.post("/add", json={"text": "machine learning model training pipeline.", "collection": "ml"})
        r = client.post("/search", json={"query": "model training", "collection": "ml", "use_bm25": True})
        for res in r.json()["results"]:
            assert "score" in res


# ── Backup / Restore ──────────────────────────────────────────────────────────

class TestBackupRestore:
    def test_backup_returns_documents(self, client):
        client.post("/add", json={
            "text": "This document will be backed up.",
            "collection": "backup-src",
            "source": "https://example.com/page",
        })
        r = client.get("/backup/backup-src")
        assert r.status_code == 200
        body = r.json()
        assert body["collection"] == "backup-src"
        assert body["doc_count"] >= 1
        assert len(body["documents"]) >= 1
        assert "exported_at" in body

    def test_backup_document_has_expected_fields(self, client):
        client.post("/add", json={
            "text": "Backup field check document.",
            "collection": "backup-fields",
            "source": "https://test.com",
        })
        r = client.get("/backup/backup-fields")
        doc = r.json()["documents"][0]
        assert "text" in doc
        assert "source" in doc
        assert "hash" in doc

    def test_backup_missing_collection_returns_404(self, client):
        r = client.get("/backup/no-such-collection")
        assert r.status_code == 404

    def test_restore_adds_documents(self, client):
        docs = [
            {"text": "First restored document.", "source": "backup://test", "tags": []},
            {"text": "Second restored document.", "source": "backup://test", "tags": []},
        ]
        r = client.post("/restore", json={"collection": "restored", "documents": docs})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "restored"
        assert body["added"] == 2

    def test_restore_empty_documents_returns_400(self, client):
        r = client.post("/restore", json={"collection": "restored", "documents": []})
        assert r.status_code == 400

    def test_backup_restore_roundtrip(self, client):
        """Full roundtrip: add → backup → delete → restore → search."""
        original_text = "Roundtrip integration test: the quick brown fox."

        # 1. Add to source collection
        client.post("/add", json={"text": original_text, "collection": "roundtrip-src"})

        # 2. Back it up
        backup = client.get("/backup/roundtrip-src").json()
        assert backup["doc_count"] >= 1

        # 3. Delete source
        client.delete("/collections/roundtrip-src")

        # 4. Restore into new collection
        r = client.post("/restore", json={
            "collection": "roundtrip-dst",
            "documents": backup["documents"],
        })
        assert r.json()["added"] >= 1

        # 5. Search restored collection
        r = client.post("/search", json={"query": "quick brown fox", "collection": "roundtrip-dst"})
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) > 0
        assert any("fox" in res["text"] for res in results)

    def test_restore_skips_blank_text(self, client):
        docs = [
            {"text": "Valid document.", "source": "test"},
            {"text": "   ", "source": "test"},   # blank — should be skipped
            {"text": "", "source": "test"},       # empty — should be skipped
        ]
        r = client.post("/restore", json={"collection": "skip-blank", "documents": docs})
        body = r.json()
        assert body["added"] == 1
        assert body["skipped"] == 2


# ── Crawl tasks ───────────────────────────────────────────────────────────────

class TestCrawlTasks:
    def test_crawl_returns_task_id(self, client):
        """Starting a crawl returns a stable, deterministic task_id."""
        # crawl_site is imported inside the route handler from the crawler module
        with patch("crawler.crawl_site", new_callable=AsyncMock):
            r = client.post("/crawl", json={
                "url": "http://example.com/docs",
                "collection": "crawl-coll",
            })
        assert r.status_code == 200
        body = r.json()
        assert "task_id" in body
        assert body["status"] in ("started", "already_running")

    def test_crawl_task_id_is_deterministic(self, client):
        """Same URL + collection always produces the same task_id."""
        import server
        id1 = server._crawl_task_id("http://example.com/docs", "my-coll")
        id2 = server._crawl_task_id("http://example.com/docs", "my-coll")
        assert id1 == id2

    def test_crawl_status_unknown_task_returns_404(self, client):
        r = client.get("/crawl/deadbeef")
        assert r.status_code == 404


# ── Crawl checkpoint (unit-style) ─────────────────────────────────────────────

class TestCrawlCheckpoint:
    def test_save_and_load_checkpoint(self, tmp_path):
        from crawler import save_checkpoint, load_checkpoint

        path = tmp_path / "ckpt.json"
        visited = {"http://a.com", "http://b.com"}
        queue = ["http://c.com", "http://d.com"]

        save_checkpoint(path, visited, queue)

        loaded_visited, loaded_queue = load_checkpoint(path)
        assert loaded_visited == visited
        assert loaded_queue == queue

    def test_load_missing_checkpoint_returns_empty(self, tmp_path):
        from crawler import load_checkpoint

        visited, queue = load_checkpoint(tmp_path / "nonexistent.json")
        assert visited == set()
        assert queue == []

    def test_load_corrupt_checkpoint_returns_empty(self, tmp_path):
        from crawler import load_checkpoint

        path = tmp_path / "bad.json"
        path.write_text("NOT JSON {{{}}")

        visited, queue = load_checkpoint(path)
        assert visited == set()
        assert queue == []

    def test_checkpoint_deleted_after_full_crawl(self, tmp_path):
        """Checkpoint file must be removed when crawl completes successfully."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from crawler import save_checkpoint

        checkpoint = tmp_path / "task.json"
        save_checkpoint(checkpoint, {"http://visited.com"}, [])
        assert checkpoint.exists()

        async def run():
            import crawler
            with patch("crawler.httpx.AsyncClient") as mock_client_cls:
                # Make the HTTP client return no pages so the crawl exits immediately
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.get = AsyncMock(return_value=MagicMock(
                    status_code=404, headers={}, text="",
                ))
                mock_client_cls.return_value = mock_client

                state = {"pages_crawled": 0}
                with patch("server.get_collection", return_value=MagicMock(optimize=MagicMock())):
                    await crawler.crawl_site(
                        url="http://example.com/docs",
                        coll_name="test",
                        task_state=state,
                        max_pages=1,
                        add_fn=lambda *a, **kw: {"status": "added", "hash": "abc"},
                        checkpoint_path=checkpoint,
                    )

        asyncio.run(run())
        assert not checkpoint.exists(), "Checkpoint should be deleted after crawl completes"
