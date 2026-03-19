"""
Shared pytest fixtures and test infrastructure for Context Engine.

Installs a fake 'zvec' module into sys.modules before any server code is
imported, so tests run without the real vector database.
"""

import re
import sys
import types
from pathlib import Path
import numpy as np


# ── Fake zvec module ──────────────────────────────────────────────────────────

class _FakeDoc:
    def __init__(self, id, vectors, fields):
        self.id = id
        self.vectors = vectors   # dict: field_name -> list[float]
        self.fields = fields


class _FakeResult:
    def __init__(self, fields, score):
        self.fields = fields
        self.score = score


class _FakeStats:
    def __init__(self, count):
        self.doc_count = count


class _FakeCollection:
    def __init__(self):
        self._docs: dict[str, _FakeDoc] = {}

    def insert(self, doc: _FakeDoc):
        self._docs[doc.id] = doc

    def query(self, vectors=None, filter=None, topk=10):
        if filter is not None:
            # Parse 'field == "value"' expressions used for dedup checks
            m = re.match(r'(\w+) == "([^"]+)"', filter)
            if m:
                field, value = m.group(1), m.group(2)
                return [
                    _FakeResult(doc.fields, 1.0)
                    for doc in self._docs.values()
                    if doc.fields.get(field) == value
                ][:topk]
            return []

        if vectors is not None:
            query_vec = np.array(vectors.vector, dtype="float32")
            results = []
            for doc in self._docs.values():
                emb = doc.vectors.get("embedding")
                if emb is None:
                    continue
                doc_vec = np.array(emb, dtype="float32")
                if doc_vec.shape == query_vec.shape:
                    score = float(np.dot(query_vec, doc_vec))
                    results.append(_FakeResult(doc.fields, score))
            results.sort(key=lambda r: r.score, reverse=True)
            return results[:topk]

        return []

    def optimize(self):
        pass

    @property
    def stats(self):
        return _FakeStats(len(self._docs))


class _FakeVectorQuery:
    def __init__(self, field_name, vector):
        self.field_name = field_name
        self.vector = vector


class _DataType:
    STRING = "STRING"
    ARRAY_STRING = "ARRAY_STRING"
    INT64 = "INT64"
    VECTOR_FP32 = "VECTOR_FP32"


def _noop(*a, **kw):
    pass


class _Schema:
    def __init__(self, *a, **kw):
        pass


_fake_collections: dict[str, _FakeCollection] = {}


def _fake_open(path, option=None):
    # Create directory if missing (the real zvec does this too)
    Path(path).mkdir(parents=True, exist_ok=True)
    if path not in _fake_collections:
        _fake_collections[path] = _FakeCollection()
    return _fake_collections[path]


def _fake_create_and_open(path, schema=None, option=None):
    # Create the collection directory on disk so sidecar files can be written
    Path(path).mkdir(parents=True, exist_ok=True)
    _fake_collections[path] = _FakeCollection()
    return _fake_collections[path]


def _install_fake_zvec():
    mod = types.ModuleType("zvec")
    mod.Collection = _FakeCollection
    mod.CollectionSchema = _Schema
    mod.FieldSchema = _Schema
    mod.VectorSchema = _Schema
    mod.DataType = _DataType
    mod.CollectionOption = _Schema
    mod.Doc = _FakeDoc
    mod.VectorQuery = _FakeVectorQuery
    mod.open = _fake_open
    mod.create_and_open = _fake_create_and_open
    sys.modules["zvec"] = mod


# ── Fake sentence_transformers module ────────────────────────────────────────

def _install_fake_sentence_transformers():
    """Stub out sentence_transformers so server.py can be imported without the real model."""
    class _FakeSentenceTransformer:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts, normalize_embeddings=True):
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

    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = _FakeSentenceTransformer
    sys.modules["sentence_transformers"] = mod


# Install both fakes immediately so any subsequent `import server` gets them
_install_fake_zvec()
_install_fake_sentence_transformers()
# Reset per-session collection store so tests start clean
_fake_collections.clear()
